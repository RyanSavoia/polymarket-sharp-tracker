"""
Polymarket Sharp Bettor Tracker Bot - Focused Version

This bot tracks profitable bettors on Polymarket and alerts when they make new bets.
Optimized to focus on known whales and sports markets only.
"""

import asyncio
import json
import logging
import os
import time
import re
import gc
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, asdict
import aiohttp
import tweepy
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import schedule
from decimal import Decimal
import sqlite3
from dotenv import load_dotenv
from flask import Flask
import threading

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
POLYMARKET_API_BASE = "https://gamma-api.polymarket.com"
POLYMARKET_BASE_URL = "https://polymarket.com"

# Load from environment or use defaults
TWITTER_ENABLED = os.getenv('TWITTER_ENABLED', 'false').lower() == 'true'
MIN_PNL_SHARP = float(os.getenv('MIN_PNL_SHARP', '10000'))
MIN_ROI_SHARP = float(os.getenv('MIN_ROI_SHARP', '10'))
MIN_VOLUME_SHARP = float(os.getenv('MIN_VOLUME_SHARP', '50000'))
MIN_BET_ALERT = float(os.getenv('MIN_BET_ALERT', '5000'))

# Scanning configuration
SCAN_LEADERBOARD = os.getenv('SCAN_LEADERBOARD', 'true').lower() == 'true'
SCAN_KNOWN_WHALES_ONLY = os.getenv('SCAN_KNOWN_WHALES_ONLY', 'false').lower() == 'true'
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '10'))
MAX_NEW_WALLETS_PER_SCAN = int(os.getenv('MAX_NEW_WALLETS_PER_SCAN', '10'))

# Create a simple web server for health checks
app = Flask(__name__)

@app.route('/')
def health_check():
    return 'Polymarket Sharp Bettor Tracker is running!', 200

@app.route('/status')
def status():
    """Return bot status"""
    try:
        db = DatabaseManager()
        stats = db.get_scanning_stats()
        return {
            'status': 'running',
            'sharp_bettors': stats.get('sharp_bettors', 0),
            'total_bettors': stats.get('total_bettors', 0),
            'total_sightings': stats.get('total_sightings', 0)
        }, 200
    except Exception as e:
        return {'status': 'error', 'message': str(e)}, 500

def run_flask():
    """Run Flask server for health checks"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

@dataclass
class BettorProfile:
    """Data class for bettor profile information"""
    wallet_address: str
    username: Optional[str]
    total_pnl: float
    total_volume: float
    markets_traded: int
    positions_value: float
    roi: float
    last_updated: datetime
    leaderboard_rank: Optional[int] = None

class PolymarketAPI:
    """Handles all Polymarket API interactions"""
    
    def __init__(self):
        self.session = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            
    async def get_sports_markets(self) -> List[Dict]:
        """Fetch ONLY sports game markets using Polymarket's actual format"""
        try:
            all_markets = []
            
            # Get all markets
            url = f"{POLYMARKET_API_BASE}/markets"
            params = {"active": "true", "closed": "false", "limit": 1000}
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"API returned {len(data)} total markets")
                    
                    for market in data:
                        title = market.get('question', market.get('title', '')).lower()
                        slug = market.get('slug', '').lower()
                        
                        # Look for team abbreviations in slug/title
                        # MLB: tex, nyy, bal, pit, etc.
                        # NBA: lal, bos, gsw, etc.
                        # NFL: dal, ne, gb, etc.
                        
                        # Common team abbreviations
                        team_abbreviations = [
                            # MLB
                            'nyy', 'bos', 'bal', 'tb', 'tor', 'cws', 'cle', 'det', 
                            'kc', 'min', 'hou', 'oak', 'sea', 'tex', 'laa', 'atl',
                            'mia', 'nym', 'phi', 'was', 'chc', 'cin', 'mil', 'pit',
                            'stl', 'ari', 'col', 'la', 'sd', 'sf',
                            # NBA
                            'atl', 'bos', 'bkn', 'cha', 'chi', 'cle', 'dal', 'den',
                            'det', 'gsw', 'hou', 'ind', 'lac', 'lal', 'mem', 'mia',
                            'mil', 'min', 'no', 'ny', 'okc', 'orl', 'phi', 'phx',
                            'por', 'sac', 'sa', 'tor', 'uta', 'was',
                            # NFL
                            'ari', 'atl', 'bal', 'buf', 'car', 'chi', 'cin', 'cle',
                            'dal', 'den', 'det', 'gb', 'hou', 'ind', 'jax', 'kc',
                            'lv', 'lac', 'lar', 'mia', 'min', 'ne', 'no', 'nyg',
                            'nyj', 'phi', 'pit', 'sf', 'sea', 'tb', 'ten', 'was',
                            # NHL
                            'ana', 'ari', 'bos', 'buf', 'cgy', 'car', 'chi', 'col',
                            'cbj', 'dal', 'det', 'edm', 'fla', 'la', 'min', 'mtl',
                            'nsh', 'nj', 'nyi', 'nyr', 'ott', 'phi', 'pit', 'sj',
                            'stl', 'tb', 'tor', 'van', 'vgk', 'wpg', 'wsh'
                        ]
                        
                        # Count team abbreviations found
                        teams_found = sum(1 for team in team_abbreviations if team in slug or team in title)
                        
                        # Look for sport indicators in slug
                        has_mlb = 'mlb' in slug or 'baseball' in title
                        has_nba = 'nba' in slug or 'basketball' in title
                        has_nfl = 'nfl' in slug or 'football' in title
                        has_nhl = 'nhl' in slug or 'hockey' in title
                        
                        has_sport = has_mlb or has_nba or has_nfl or has_nhl
                        
                        # BANNED words - absolutely no politics or futures
                        banned_words = [
                            'trump', 'biden', 'election', 'president', 'politics',
                            'championship', 'finals', 'mvp', 'season', 'futures',
                            'winner', 'win', 'draft', 'award', 'total', 'prop'
                        ]
                        
                        has_banned = any(banned in title or banned in slug for banned in banned_words)
                        
                        # Accept if: has sport indicator AND has 2+ team abbreviations AND no banned words
                        if has_sport and teams_found >= 2 and not has_banned:
                            logger.info(f"✅ ACCEPTED game: {title[:60]} | slug: {slug[:40]}")
                            
                            # Categorize
                            if has_mlb:
                                market['category'] = 'MLB'
                            elif has_nba:
                                market['category'] = 'NBA'
                            elif has_nfl:
                                market['category'] = 'NFL'
                            elif has_nhl:
                                market['category'] = 'NHL'
                                
                            all_markets.append(market)
                            
                        # Log rejections for debugging
                        elif 'trump' in title or 'election' in title:
                            logger.debug(f"❌ Rejected politics: {title[:40]}")
                        elif teams_found < 2:
                            logger.debug(f"❌ Rejected - not enough teams: {title[:40]}")
                            
            logger.info(f"Filtered to {len(all_markets)} actual sports games")
            return all_markets
            
        except Exception as e:
            logger.error(f"Error fetching sports markets: {e}")
            return []

class PolymarketScraper:
    """Handles web scraping with focus on known whales"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver = None
        
    def __enter__(self):
        options = Options()
        if self.headless:
            options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        # Memory optimization
        options.add_argument('--memory-pressure-off')
        options.add_argument('--disable-background-timer-throttling')
        options.add_argument('--disable-features=TranslateUI')
        
        # Disable images to save memory
        prefs = {'profile.managed_default_content_settings.images': 2}
        options.add_experimental_option('prefs', prefs)
        
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
        except:
            self.driver = webdriver.Chrome(options=options)
            
        self.driver.set_page_load_timeout(30)
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
                
    def get_leaderboard_whales(self) -> List[Tuple[str, int]]:
        """Scrape top traders from Polymarket leaderboard"""
        try:
            logger.info("Scanning Polymarket leaderboard for top traders...")
            self.driver.get(f"{POLYMARKET_BASE_URL}/leaderboard")
            
            time.sleep(3)  # Wait for page to load
            
            whale_wallets = []
            
            # Look for leaderboard entries
            # Try different possible selectors
            selectors = [
                "a[href*='/profile/0x']",
                ".leaderboard-row a",
                "[class*='leaderboard'] a",
                "table a[href*='profile']"
            ]
            
            for selector in selectors:
                links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if links:
                    logger.info(f"Found {len(links)} potential whale links with selector: {selector}")
                    for i, link in enumerate(links[:50], 1):  # Top 50 traders
                        href = link.get_attribute('href')
                        if href and '/profile/0x' in href:
                            wallet = href.split('/profile/')[-1].split('?')[0].lower()
                            if wallet.startswith('0x') and len(wallet) == 42:
                                whale_wallets.append((wallet, i))  # wallet, rank
                    break
                    
            logger.info(f"Found {len(whale_wallets)} whales from leaderboard")
            return whale_wallets
            
        except Exception as e:
            logger.error(f"Error scraping leaderboard: {e}")
            return []
            
    def check_market_for_whales(self, market_url: str, target_wallets: Set[str]) -> List[Tuple[str, str, str]]:
        """Check if any target whales are in this market and which side they bet"""
        try:
            logger.info(f"Checking market for whales: {market_url}")
            self.driver.get(market_url)
            
            time.sleep(3)
            
            # First check if we need to click "Game view" or similar
            try:
                game_view_selectors = [
                    "button:contains('Game view')",
                    "a:contains('Game view')",
                    "[class*='game-view']",
                    "button:contains('View Game')",
                    "a:contains('View Market')"
                ]
                
                for selector in game_view_selectors:
                    try:
                        # Try CSS selector
                        elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        if not elements:
                            # Try XPath for text content
                            elements = self.driver.find_elements(By.XPATH, f"//*[contains(text(), 'Game view')]")
                        
                        if elements:
                            logger.info("Found 'Game view' button, clicking...")
                            elements[0].click()
                            time.sleep(3)
                            break
                    except:
                        continue
            except Exception as e:
                logger.debug(f"No game view button found: {e}")
            
            # Now look for holders/activity sections
            found_whales = []
            
            # Try to find holders or activity tabs/sections
            tab_selectors = [
                "button:contains('Holders')",
                "button:contains('Activity')",
                "button:contains('Positions')",
                "[role='tab']",
                "[class*='tab']"
            ]
            
            for selector in tab_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if not elements:
                        elements = self.driver.find_elements(By.XPATH, f"//*[contains(text(), 'Holders') or contains(text(), 'Activity')]")
                    
                    for element in elements:
                        text = element.text
                        if 'holders' in text.lower() or 'activity' in text.lower():
                            logger.info(f"Found {text} tab, clicking...")
                            element.click()
                            time.sleep(2)
                            break
                except:
                    continue
            
            # Get the market question
            market_question = None
            try:
                question_selectors = ["h1", "[class*='title']", "[class*='question']"]
                for selector in question_selectors:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements and elements[0].text:
                        market_question = elements[0].text
                        break
            except:
                pass
            
            # Now look for profile links in holders/activity section
            profile_links = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/profile/']")
            
            if not profile_links:
                # Try alternate selectors
                profile_links = self.driver.find_elements(By.XPATH, "//a[contains(@href, '/profile/')]")
            
            logger.info(f"Found {len(profile_links)} profile links on page")
            
            # If still no whales but we're not tracking any yet, get top holders
            if not target_wallets and len(profile_links) > 0:
                logger.info("No target wallets yet, will check top holders")
                # Get first 10 profile links as potential whales
                for link in profile_links[:10]:
                    href = link.get_attribute('href')
                    if href and '/profile/0x' in href:
                        wallet = href.split('/profile/')[-1].split('?')[0].lower()
                        if wallet.startswith('0x') and len(wallet) == 42:
                            # Try to determine YES/NO from context
                            side = 'UNKNOWN'
                            try:
                                parent = link.find_element(By.XPATH, "../..")
                                parent_text = parent.text
                                if 'YES' in parent_text:
                                    side = 'YES'
                                elif 'NO' in parent_text:
                                    side = 'NO'
                            except:
                                pass
                            found_whales.append((wallet, side, market_question))
            else:
                # Check if any known whales are here
                for link in profile_links:
                    href = link.get_attribute('href')
                    if href and '/profile/0x' in href:
                        wallet = href.split('/profile/')[-1].split('?')[0].lower()
                        if wallet in target_wallets:
                            # Try to determine YES/NO
                            side = 'UNKNOWN'
                            try:
                                parent = link.find_element(By.XPATH, "../..")
                                parent_text = parent.text
                                if 'YES' in parent_text:
                                    side = 'YES'
                                elif 'NO' in parent_text:
                                    side = 'NO'
                            except:
                                pass
                            found_whales.append((wallet, side, market_question))
            
            if found_whales:
                logger.info(f"Found {len(found_whales)} whales in market")
            else:
                logger.info("No whales found in this market")
                
            return found_whales
            
        except Exception as e:
            logger.error(f"Error checking market for whales: {e}")
            return []
            
    def get_market_top_holders(self, market_url: str, limit: int = 5) -> List[str]:
        """Get only the top holders from a market"""
        try:
            self.driver.get(market_url)
            time.sleep(2)
            
            # Look for holder/position sections
            # These are usually the biggest positions
            top_wallets = []
            
            # Try to find a positions or holders table/list
            position_selectors = [
                ".positions-list a[href*='profile']",
                ".top-holders a[href*='profile']",
                "[class*='position'] a[href*='profile']",
                "a[href*='profile/0x']"  # Fallback
            ]
            
            for selector in position_selectors:
                links = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if links:
                    for link in links[:limit]:  # Only top N
                        href = link.get_attribute('href')
                        if href and '/profile/0x' in href:
                            wallet = href.split('/profile/')[-1].split('?')[0].lower()
                            if wallet.startswith('0x') and len(wallet) == 42:
                                top_wallets.append(wallet)
                    break
                    
            return top_wallets
            
        except Exception as e:
            logger.error(f"Error getting top holders: {e}")
            return []
            
    def get_user_profile_data(self, wallet_address: str, rank: Optional[int] = None) -> Optional[BettorProfile]:
        """Scrape user profile to get P&L data"""
        try:
            url = f"{POLYMARKET_BASE_URL}/profile/{wallet_address}"
            self.driver.get(url)
            
            time.sleep(2)
            
            # Extract all text
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            lines = page_text.split('\n')
            
            # Parse profile data
            data = {}
            for i, line in enumerate(lines):
                if 'Profit/loss' in line and i+1 < len(lines):
                    data['pnl'] = lines[i+1]
                elif 'Volume traded' in line and i+1 < len(lines):
                    data['volume'] = lines[i+1]
                elif 'Markets traded' in line and i+1 < len(lines):
                    data['markets'] = lines[i+1]
                elif 'Positions value' in line and i+1 < len(lines):
                    data['positions'] = lines[i+1]
                    
            # Get username
            username = None
            for i, line in enumerate(lines):
                if wallet_address[:6].lower() in line.lower():
                    if i > 0:
                        potential_username = lines[i-1]
                        if not any(x in potential_username.lower() for x in ['joined', 'positions', 'profit', 'volume']):
                            username = potential_username
                    break
                    
            # Parse numeric values
            def parse_money(value_str):
                if not value_str:
                    return 0.0
                clean = value_str.replace('$', '').replace(',', '')
                if '(' in clean and ')' in clean:
                    clean = '-' + clean.replace('(', '').replace(')', '')
                try:
                    return float(clean)
                except:
                    return 0.0
                    
            pnl = parse_money(data.get('pnl', '0'))
            volume = parse_money(data.get('volume', '0'))
            positions_value = parse_money(data.get('positions', '0'))
            
            try:
                markets_traded = int(data.get('markets', '0').replace(',', ''))
            except:
                markets_traded = 0
                
            # Calculate ROI
            roi = 0
            if volume > 0:
                roi = (pnl / volume) * 100
                
            logger.info(f"Profile {wallet_address}: P&L=${pnl:,.2f}, Volume=${volume:,.2f}, ROI={roi:.1f}%")
                
            return BettorProfile(
                wallet_address=wallet_address,
                username=username,
                total_pnl=pnl,
                total_volume=volume,
                markets_traded=markets_traded,
                positions_value=positions_value,
                roi=roi,
                last_updated=datetime.utcnow(),
                leaderboard_rank=rank
            )
            
        except Exception as e:
            logger.error(f"Error scraping user profile {wallet_address}: {e}")
            return None
        finally:
            gc.collect()

class DatabaseManager:
    """Manages SQLite database for storing bettor and position data"""
    
    def __init__(self, db_path: str = "polymarket_tracker.db"):
        self.db_path = db_path
        self.init_database()
        
    def init_database(self):
        """Initialize database tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Bettors table with leaderboard rank
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bettors (
                    wallet_address TEXT PRIMARY KEY,
                    username TEXT,
                    total_pnl REAL,
                    total_volume REAL,
                    markets_traded INTEGER,
                    positions_value REAL,
                    roi REAL,
                    last_updated TIMESTAMP,
                    is_sharp BOOLEAN DEFAULT 0,
                    leaderboard_rank INTEGER,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Whale sightings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS whale_sightings (
                    wallet_address TEXT,
                    market_url TEXT,
                    market_category TEXT,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    PRIMARY KEY (wallet_address, market_url)
                )
            """)
            
            # Alerts sent table to avoid duplicates
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts_sent (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT,
                    market_url TEXT,
                    alert_timestamp TIMESTAMP,
                    UNIQUE(wallet_address, market_url)
                )
            """)
            
            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bettors_sharp ON bettors(is_sharp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bettors_pnl ON bettors(total_pnl DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sightings_first_seen ON whale_sightings(first_seen)")
            
            conn.commit()
            
    def update_bettor(self, bettor: BettorProfile):
        """Update or insert bettor profile"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Check if bettor qualifies as sharp
            is_sharp = (
                bettor.total_pnl > MIN_PNL_SHARP and
                bettor.roi > MIN_ROI_SHARP and
                bettor.total_volume > MIN_VOLUME_SHARP
            )
            
            cursor.execute("""
                INSERT OR REPLACE INTO bettors 
                (wallet_address, username, total_pnl, total_volume, markets_traded, 
                 positions_value, roi, last_updated, is_sharp, leaderboard_rank)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bettor.wallet_address, bettor.username, bettor.total_pnl,
                bettor.total_volume, bettor.markets_traded, bettor.positions_value,
                bettor.roi, bettor.last_updated, is_sharp, bettor.leaderboard_rank
            ))
            
            conn.commit()
            
            if is_sharp:
                logger.info(f"💎 Sharp bettor: {bettor.wallet_address} - P&L: ${bettor.total_pnl:,.0f}, ROI: {bettor.roi:.1f}%")
                
    def get_known_sharp_wallets(self) -> Set[str]:
        """Get set of known sharp bettor wallets"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT wallet_address FROM bettors 
                WHERE is_sharp = 1
            """)
            return set(row[0] for row in cursor.fetchall())
            
    def get_all_tracked_wallets(self) -> Set[str]:
        """Get all wallets we're tracking (sharp or potential)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT wallet_address FROM bettors 
                WHERE total_volume > 10000  -- Active traders
                OR leaderboard_rank IS NOT NULL  -- Leaderboard traders
                OR is_sharp = 1  -- Known sharps
            """)
            return set(row[0] for row in cursor.fetchall())
            
    def record_whale_sighting(self, wallet_address: str, market_url: str, category: str, side: str = 'UNKNOWN', question: str = None):
        """Record that a whale was seen in a market with their position side"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # First add the columns if they don't exist
            cursor.execute("PRAGMA table_info(whale_sightings)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'side' not in columns:
                cursor.execute("ALTER TABLE whale_sightings ADD COLUMN side TEXT DEFAULT 'UNKNOWN'")
            if 'market_question' not in columns:
                cursor.execute("ALTER TABLE whale_sightings ADD COLUMN market_question TEXT")
            
            cursor.execute("""
                INSERT OR REPLACE INTO whale_sightings 
                (wallet_address, market_url, market_category, side, market_question, first_seen, last_seen)
                VALUES (
                    ?, ?, ?, ?, ?,
                    COALESCE((SELECT first_seen FROM whale_sightings WHERE wallet_address = ? AND market_url = ?), ?),
                    ?
                )
            """, (wallet_address, market_url, category, side, question, wallet_address, market_url, datetime.utcnow(), datetime.utcnow()))
            
            conn.commit()
            
    def get_new_sharp_positions(self, hours: int = 1) -> List[Dict]:
        """Get new positions from sharp bettors"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            cursor.execute("""
                SELECT DISTINCT
                    ws.wallet_address,
                    ws.market_url,
                    ws.market_category,
                    ws.side,
                    ws.market_question,
                    b.username,
                    b.total_pnl,
                    b.roi,
                    b.positions_value,
                    b.total_volume,
                    ws.first_seen,
                    b.leaderboard_rank
                FROM whale_sightings ws
                JOIN bettors b ON ws.wallet_address = b.wallet_address
                WHERE b.is_sharp = 1
                AND ws.first_seen > ?
                AND b.positions_value >= ?
                AND NOT EXISTS (
                    SELECT 1 FROM alerts_sent a 
                    WHERE a.wallet_address = ws.wallet_address 
                    AND a.market_url = ws.market_url
                )
                ORDER BY ws.first_seen DESC
            """, (cutoff_time, MIN_BET_ALERT))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'wallet_address': row[0],
                    'market_url': row[1],
                    'market_category': row[2],
                    'side': row[3],
                    'market_question': row[4],
                    'username': row[5],
                    'total_pnl': row[6],
                    'roi': row[7],
                    'positions_value': row[8],
                    'total_volume': row[9],
                    'timestamp': row[10],
                    'leaderboard_rank': row[11]
                })
                
            return results
            
    def mark_alert_sent(self, wallet_address: str, market_url: str):
        """Mark that an alert was sent for this position"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO alerts_sent 
                (wallet_address, market_url, alert_timestamp)
                VALUES (?, ?, ?)
            """, (wallet_address, market_url, datetime.utcnow()))
            conn.commit()
            
    def get_sharp_bettors(self) -> List[BettorProfile]:
        """Get all sharp bettors from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM bettors 
                WHERE is_sharp = 1 
                ORDER BY roi DESC
                LIMIT 50
            """)
            
            rows = cursor.fetchall()
            bettors = []
            
            for row in rows:
                bettors.append(BettorProfile(
                    wallet_address=row[0],
                    username=row[1],
                    total_pnl=row[2],
                    total_volume=row[3],
                    markets_traded=row[4],
                    positions_value=row[5],
                    roi=row[6],
                    last_updated=datetime.fromisoformat(row[7]),
                    leaderboard_rank=row[9] if len(row) > 9 else None
                ))
                
            return bettors
            
    def needs_update(self, wallet_address: str, hours: int = 6) -> bool:
        """Check if a bettor profile needs updating"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT last_updated FROM bettors 
                WHERE wallet_address = ?
            """, (wallet_address,))
            
            result = cursor.fetchone()
            
            if not result:
                return True
                
            last_updated = datetime.fromisoformat(result[0])
            return datetime.utcnow() - last_updated > timedelta(hours=hours)
            
    def get_scanning_stats(self) -> Dict:
        """Get statistics about scanning progress"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # Total bettors
            cursor.execute("SELECT COUNT(*) FROM bettors")
            stats['total_bettors'] = cursor.fetchone()[0]
            
            # Sharp bettors
            cursor.execute("SELECT COUNT(*) FROM bettors WHERE is_sharp = 1")
            stats['sharp_bettors'] = cursor.fetchone()[0]
            
            # Total sightings
            cursor.execute("SELECT COUNT(*) FROM whale_sightings")
            stats['total_sightings'] = cursor.fetchone()[0]
            
            # Recent sightings (last 24h)
            yesterday = datetime.utcnow() - timedelta(days=1)
            cursor.execute("""
                SELECT COUNT(*) FROM whale_sightings 
                WHERE first_seen > ?
            """, (yesterday,))
            stats['recent_sightings'] = cursor.fetchone()[0]
            
            return stats

class TwitterBot:
    """Handles Twitter/X posting functionality"""
    
    def __init__(self, api_key: str, api_secret: str, access_token: str, access_secret: str):
        auth = tweepy.OAuthHandler(api_key, api_secret)
        auth.set_access_token(access_token, access_secret)
        self.api = tweepy.API(auth)
        
    def post_alert(self, position_data: Dict) -> bool:
        """Post a whale alert to Twitter"""
        try:
            username = position_data.get('username', 'Whale')
            wallet_short = f"{position_data['wallet_address'][:6]}...{position_data['wallet_address'][-4:]}"
            
            # Extract market info
            market_slug = position_data['market_url'].split('/')[-1]
            market_question = position_data.get('market_question')
            side = position_data.get('side', 'UNKNOWN')
            category = position_data.get('market_category', 'SPORTS')
            
            # Get volume for context
            volume = position_data.get('total_volume', 0)
            pnl = position_data.get('total_pnl', 0)
            
            # Format the bet description based on what we know
            if market_question and side != 'UNKNOWN':
                # We have the full question, so we can be specific
                if 'lakers' in market_question.lower() and 'celtics' in market_question.lower():
                    # Example: "Will the Lakers beat the Celtics?"
                    if 'lakers beat' in market_question.lower() or 'lakers win' in market_question.lower():
                        team = 'Lakers' if side == 'YES' else 'Celtics'
                    else:
                        team = 'Celtics' if side == 'YES' else 'Lakers'
                    bet_description = f"{team} to win"
                else:
                    # For other markets, show question + side
                    bet_description = f"{market_question} ({side})"
            else:
                # Fallback to basic format
                market_title = market_slug.replace('-', ' ').title()[:50]
                bet_description = market_title
            
            message = f"""🐋 SHARP BETTOR ALERT

{username} ({wallet_short})
✅ Profitable after ${volume:,.0f} in bets
💰 Lifetime Profit: +${pnl:,.0f}

Dropped a MEGA bet on:
📍 {bet_description}
💵 Size: ${position_data['positions_value']:,.0f}

#Polymarket #SharpMoney #{category}"""

            if TWITTER_ENABLED:
                self.api.update_status(message)
                logger.info(f"Posted alert for {wallet_short}")
                return True
            else:
                logger.info(f"Twitter disabled - would have posted: {message}")
                return True
                
        except Exception as e:
            logger.error(f"Error posting to Twitter: {e}")
            return False
            
    def post_leaderboard(self, bettors: List[BettorProfile]) -> bool:
        """Post daily leaderboard"""
        try:
            message = "🏆 TOP SHARP SPORTS BETTORS\n\n"
            
            for i, bettor in enumerate(bettors[:10], 1):
                username = bettor.username or f"{bettor.wallet_address[:8]}..."
                rank_emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                message += f"{rank_emoji} {username}\n"
                message += f"   💰 +${bettor.total_pnl:,.0f} ({bettor.roi:.1f}% ROI)\n"
                
            message += "\n#Polymarket #SportsBetting"
            
            if TWITTER_ENABLED:
                self.api.update_status(message)
                logger.info("Posted daily leaderboard")
                return True
            else:
                logger.info(f"Twitter disabled - would have posted leaderboard")
                return True
                
        except Exception as e:
            logger.error(f"Error posting leaderboard: {e}")
            return False

class PolymarketTracker:
    """Main tracker class focused on known whales"""
    
    def __init__(self, twitter_credentials: Optional[Dict] = None):
        self.db = DatabaseManager()
        self.twitter_bot = None
        
        if twitter_credentials and all(twitter_credentials.values()):
            self.twitter_bot = TwitterBot(
                twitter_credentials['api_key'],
                twitter_credentials['api_secret'],
                twitter_credentials['access_token'],
                twitter_credentials['access_secret']
            )
        else:
            logger.warning("Twitter credentials not provided or incomplete")
            
    async def scan_leaderboard(self):
        """Scan leaderboard for new whales"""
        if not SCAN_LEADERBOARD:
            return
            
        logger.info("📊 Scanning leaderboard for top traders...")
        
        with PolymarketScraper(headless=True) as scraper:
            # Get top traders from leaderboard
            leaderboard_whales = scraper.get_leaderboard_whales()
            
            if not leaderboard_whales:
                logger.warning("No whales found on leaderboard")
                return
                
            # Check profiles of new whales
            new_whales_checked = 0
            for wallet, rank in leaderboard_whales:
                if self.db.needs_update(wallet) and new_whales_checked < MAX_NEW_WALLETS_PER_SCAN:
                    profile = scraper.get_user_profile_data(wallet, rank)
                    if profile:
                        self.db.update_bettor(profile)
                        new_whales_checked += 1
                        time.sleep(1)  # Rate limiting
                        
            logger.info(f"✅ Updated {new_whales_checked} whale profiles from leaderboard")
            
    async def scan_sports_markets(self):
        """Scan sports markets for whale activity"""
        logger.info("🏈 Starting sports market scan...")
        
        # Get sports markets only
        async with PolymarketAPI() as api:
            markets = await api.get_sports_markets()
            
        if not markets:
            logger.warning("No sports markets found")
            return
            
        # Get known whales to track
        if SCAN_KNOWN_WHALES_ONLY:
            target_wallets = self.db.get_known_sharp_wallets()
            logger.info(f"🎯 Tracking {len(target_wallets)} known sharp bettors")
        else:
            target_wallets = self.db.get_all_tracked_wallets()
            logger.info(f"🎯 Tracking {len(target_wallets)} known wallets")
            
        if not target_wallets:
            logger.warning("No known whales to track. Run with SCAN_LEADERBOARD=true first")
            return
            
        # Process markets in batches
        whales_found = 0
        markets_checked = 0
        
        for batch_start in range(0, len(markets), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(markets))
            batch = markets[batch_start:batch_end]
            
            logger.info(f"📦 Processing batch: markets {batch_start+1}-{batch_end}")
            
            with PolymarketScraper(headless=True) as scraper:
                for market in batch:
                    try:
                        market_url = f"{POLYMARKET_BASE_URL}/event/{market.get('slug', '')}"
                        category = market.get('category', 'SPORTS')
                        
                        # Check if any known whales are in this market
                        found_whales = scraper.check_market_for_whales(market_url, target_wallets)
                        
                        if found_whales:
                            logger.info(f"🎯 Found {len(found_whales)} whales in {market.get('title', 'Unknown')}")
                            whales_found += len(found_whales)
                            
                            for wallet, side, question in found_whales:
                                self.db.record_whale_sighting(wallet, market_url, category, side, question)
                                
                        # Optionally check top holders if not enough whales found
                        elif not SCAN_KNOWN_WHALES_ONLY and markets_checked < 20:
                            top_holders = scraper.get_market_top_holders(market_url, limit=3)
                            for wallet in top_holders:
                                if wallet not in target_wallets and self.db.needs_update(wallet):
                                    profile = scraper.get_user_profile_data(wallet)
                                    if profile and profile.total_pnl > 5000:  # Potential whale
                                        self.db.update_bettor(profile)
                                        
                        markets_checked += 1
                        time.sleep(0.5)  # Rate limiting
                        
                    except Exception as e:
                        logger.error(f"Error processing market: {e}")
                        continue
                        
            gc.collect()  # Clean up between batches
            
        logger.info(f"✅ Scan complete: Checked {markets_checked} markets, found {whales_found} whale positions")
        
    def check_for_alerts(self):
        """Check for new sharp bettor positions and send alerts"""
        if not self.twitter_bot:
            logger.warning("Twitter bot not configured, skipping alerts")
            return
            
        # Get new positions from sharp bettors
        new_positions = self.db.get_new_sharp_positions(hours=2)
        
        for position in new_positions:
            logger.info(f"🚨 New sharp position: {position['wallet_address']} in {position['market_category']}")
            
            # Send alert
            if self.twitter_bot.post_alert(position):
                # Mark as alerted to avoid duplicates
                self.db.mark_alert_sent(position['wallet_address'], position['market_url'])
                
            time.sleep(5)  # Rate limit Twitter posts
            
    def post_daily_leaderboard(self):
        """Post daily leaderboard of top sharp bettors"""
        if not self.twitter_bot:
            return
            
        sharp_bettors = self.db.get_sharp_bettors()
        if sharp_bettors:
            self.twitter_bot.post_leaderboard(sharp_bettors)
            
    async def run_cycle(self):
        """Run a complete scan and alert cycle"""
        try:
            logger.info("🔄 Starting tracker cycle...")
            
            # Scan leaderboard for new whales
            await self.scan_leaderboard()
            
            # Scan sports markets for whale activity
            await self.scan_sports_markets()
            
            # Check for alerts
            self.check_for_alerts()
            
            logger.info("✅ Tracker cycle completed")
            
            # Log stats
            sharp_wallets = self.db.get_known_sharp_wallets()
            logger.info(f"📊 Stats: Tracking {len(sharp_wallets)} sharp bettors")
            
            gc.collect()
            
        except Exception as e:
            logger.error(f"Error in tracker cycle: {e}")

def main():
    """Main entry point"""
    # Start Flask in a separate thread for health checks
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("🌐 Health check server started")
    
    # Load configuration
    twitter_creds = {
        'api_key': os.getenv('TWITTER_API_KEY'),
        'api_secret': os.getenv('TWITTER_API_SECRET'),
        'access_token': os.getenv('TWITTER_ACCESS_TOKEN'),
        'access_secret': os.getenv('TWITTER_ACCESS_SECRET')
    }
    
    # Initialize tracker
    tracker = PolymarketTracker(twitter_creds)
    
    # Log configuration
    logger.info("🚀 Polymarket Sharp Bettor Tracker Starting...")
    logger.info(f"📋 Configuration:")
    logger.info(f"   - Scan leaderboard: {SCAN_LEADERBOARD}")
    logger.info(f"   - Known whales only: {SCAN_KNOWN_WHALES_ONLY}")
    logger.info(f"   - Twitter enabled: {TWITTER_ENABLED}")
    logger.info(f"   - Min P&L for sharp: ${MIN_PNL_SHARP:,.0f}")
    logger.info(f"   - Min ROI for sharp: {MIN_ROI_SHARP}%")
    
    # Run initial scan
    logger.info("🏃 Running initial scan...")
    asyncio.run(tracker.run_cycle())
    
    # Schedule regular scans
    schedule.every(30).minutes.do(lambda: asyncio.run(tracker.run_cycle()))
    
    # Schedule daily leaderboard
    schedule.every().day.at("12:00").do(tracker.post_daily_leaderboard)
    
    logger.info("⏰ Scheduled scans every 30 minutes...")
    
    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
