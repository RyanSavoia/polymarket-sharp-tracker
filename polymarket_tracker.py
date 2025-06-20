"""
Polymarket Sharp Bettor Tracker Bot - Updated Version

This bot tracks profitable bettors on Polymarket and alerts when they make new bets.
Updated to use the simpler profile scraping approach discovered in testing.
"""

import asyncio
import json
import logging
import os
import time
import re
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
    
@dataclass
class MarketPosition:
    """Data class for a position in a market"""
    wallet_address: str
    market_url: str
    market_title: str
    position_size: float
    timestamp: datetime

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
        """Fetch all active sports markets"""
        try:
            all_markets = []
            sports_groups = ['MLB', 'NFL', 'NBA', 'NHL', 'NCAAB', 'NCAAF']
            
            for sport in sports_groups:
                url = f"{POLYMARKET_API_BASE}/events"
                params = {
                    "group": sport,
                    "active": "true",
                    "closed": "false",
                    "limit": 50
                }
                
                async with self.session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        for event in data:
                            event['sport'] = sport
                            all_markets.append(event)
                    else:
                        logger.error(f"Failed to fetch {sport} markets: {response.status}")
                        
                # Rate limiting
                await asyncio.sleep(1)
                
            logger.info(f"Found {len(all_markets)} total sports markets")
            return all_markets
            
        except Exception as e:
            logger.error(f"Error fetching sports markets: {e}")
            return []

class PolymarketScraper:
    """Handles web scraping for whale detection and P&L data"""
    
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
        
        # Use webdriver_manager for automatic driver management
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
        except:
            # Fallback to regular Chrome
            self.driver = webdriver.Chrome(options=options)
            
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            self.driver.quit()
            
    def get_market_whales(self, market_url: str) -> List[str]:
        """Get whale wallet addresses from a market page"""
        try:
            logger.info(f"Scanning market: {market_url}")
            self.driver.get(market_url)
            
            # Wait for page to load
            time.sleep(3)
            
            # Find all profile links on the page
            whale_wallets = set()
            links = self.driver.find_elements(By.TAG_NAME, "a")
            
            for link in links:
                href = link.get_attribute('href')
                if href and '/profile/0x' in href:
                    # Extract wallet address
                    wallet = href.split('/profile/')[-1].split('?')[0].lower()
                    if wallet.startswith('0x') and len(wallet) == 42:
                        whale_wallets.add(wallet)
                        
            logger.info(f"Found {len(whale_wallets)} unique wallets on {market_url}")
            return list(whale_wallets)
            
        except Exception as e:
            logger.error(f"Error scraping market whales: {e}")
            return []
            
    def get_user_profile_data(self, wallet_address: str) -> Optional[BettorProfile]:
        """Scrape user profile to get P&L data"""
        try:
            url = f"{POLYMARKET_BASE_URL}/profile/{wallet_address}"
            self.driver.get(url)
            
            # Wait for profile to load
            time.sleep(3)
            
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
                    
            # Get username if available
            username = None
            for i, line in enumerate(lines):
                if wallet_address[:6].lower() in line.lower():
                    # Username is usually right before the wallet snippet
                    if i > 0:
                        potential_username = lines[i-1]
                        if not any(x in potential_username.lower() for x in ['joined', 'positions', 'profit', 'volume']):
                            username = potential_username
                    break
                    
            # Parse numeric values
            def parse_money(value_str):
                """Parse money string to float"""
                if not value_str:
                    return 0.0
                # Remove $ and commas, handle negative values
                clean = value_str.replace('$', '').replace(',', '')
                if '(' in clean and ')' in clean:
                    # Handle format like ($1,234.56) for negative
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
                last_updated=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error scraping user profile {wallet_address}: {e}")
            return None
            
    def check_user_positions(self, wallet_address: str) -> List[Dict]:
        """Check current positions for a user"""
        try:
            url = f"{POLYMARKET_BASE_URL}/profile/{wallet_address}"
            self.driver.get(url)
            time.sleep(3)
            
            # Look for position information on the profile
            positions = []
            
            # Find elements that might contain position data
            # This is simplified - you might need to adjust based on actual HTML structure
            position_elements = self.driver.find_elements(By.CSS_SELECTOR, "[class*='position']")
            
            for elem in position_elements:
                try:
                    text = elem.text
                    if '$' in text and any(word in text.lower() for word in ['yes', 'no', 'shares']):
                        positions.append({
                            'text': text,
                            'timestamp': datetime.utcnow()
                        })
                except:
                    continue
                    
            return positions
            
        except Exception as e:
            logger.error(f"Error checking positions: {e}")
            return []

class DatabaseManager:
    """Manages SQLite database for storing bettor and position data"""
    
    def __init__(self, db_path: str = "polymarket_tracker.db"):
        self.db_path = db_path
        self.init_database()
        
    def init_database(self):
        """Initialize database tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Bettors table
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
                    is_sharp BOOLEAN DEFAULT 0
                )
            """)
            
            # Positions table (tracks whale positions over time)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT,
                    market_url TEXT,
                    market_title TEXT,
                    position_size REAL,
                    timestamp TIMESTAMP,
                    alert_sent BOOLEAN DEFAULT 0,
                    FOREIGN KEY (wallet_address) REFERENCES bettors (wallet_address)
                )
            """)
            
            # Whale sightings table (tracks which whales are in which markets)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS whale_sightings (
                    wallet_address TEXT,
                    market_url TEXT,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    PRIMARY KEY (wallet_address, market_url)
                )
            """)
            
            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_timestamp ON positions(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_positions_wallet ON positions(wallet_address)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bettors_sharp ON bettors(is_sharp)")
            
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
                 positions_value, roi, last_updated, is_sharp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bettor.wallet_address, bettor.username, bettor.total_pnl,
                bettor.total_volume, bettor.markets_traded, bettor.positions_value,
                bettor.roi, bettor.last_updated, is_sharp
            ))
            
            conn.commit()
            
            if is_sharp:
                logger.info(f"Sharp bettor identified: {bettor.wallet_address} - P&L: ${bettor.total_pnl:,.0f}, ROI: {bettor.roi:.1f}%")
                
    def record_whale_sighting(self, wallet_address: str, market_url: str):
        """Record that a whale was seen in a market"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO whale_sightings 
                (wallet_address, market_url, first_seen, last_seen)
                VALUES (
                    ?, ?, 
                    COALESCE((SELECT first_seen FROM whale_sightings WHERE wallet_address = ? AND market_url = ?), ?),
                    ?
                )
            """, (wallet_address, market_url, wallet_address, market_url, datetime.utcnow(), datetime.utcnow()))
            
            conn.commit()
            
    def get_new_sharp_positions(self, hours: int = 1) -> List[Dict]:
        """Get new positions from sharp bettors in the last N hours"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            cursor.execute("""
                SELECT DISTINCT
                    ws.wallet_address,
                    ws.market_url,
                    b.username,
                    b.total_pnl,
                    b.roi,
                    b.positions_value,
                    ws.first_seen
                FROM whale_sightings ws
                JOIN bettors b ON ws.wallet_address = b.wallet_address
                WHERE b.is_sharp = 1
                AND ws.first_seen > ?
                AND b.positions_value >= ?
                ORDER BY ws.first_seen DESC
            """, (cutoff_time, MIN_BET_ALERT))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'wallet_address': row[0],
                    'market_url': row[1],
                    'username': row[2],
                    'total_pnl': row[3],
                    'roi': row[4],
                    'positions_value': row[5],
                    'timestamp': row[6]
                })
                
            return results
            
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
                    last_updated=datetime.fromisoformat(row[7])
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

class TwitterBot:
    """Handles Twitter/X posting functionality"""
    
    def __init__(self, api_key: str, api_secret: str, access_token: str, access_secret: str):
        auth = tweepy.OAuthHandler(api_key, api_secret)
        auth.set_access_token(access_token, access_secret)
        self.api = tweepy.API(auth)
        
    def post_alert(self, position_data: Dict) -> bool:
        """Post a whale alert to Twitter"""
        try:
            # Format the alert message
            username = position_data.get('username', 'Whale')
            wallet_short = f"{position_data['wallet_address'][:6]}...{position_data['wallet_address'][-4:]}"
            
            # Extract market title from URL
            market_slug = position_data['market_url'].split('/')[-1]
            market_title = market_slug.replace('-', ' ').title()
            
            message = f"""🐋 SHARP BETTOR ALERT

{username} ({wallet_short})
💰 Lifetime P&L: +${position_data['total_pnl']:,.0f}
📊 ROI: {position_data['roi']:.1f}%

New position detected:
📍 {market_title}
💵 Position Value: ${position_data['positions_value']:,.0f}

#Polymarket #SharpMoney #SportsBetting"""

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
            message = "🏆 TOP SHARP BETTORS ON POLYMARKET\n\n"
            
            for i, bettor in enumerate(bettors[:10], 1):
                username = bettor.username or f"{bettor.wallet_address[:8]}..."
                message += f"{i}. {username}\n"
                message += f"   💰 P&L: +${bettor.total_pnl:,.0f} ({bettor.roi:.1f}% ROI)\n"
                
            message += "\n#Polymarket #SharpMoney"
            
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
    """Main tracker class that coordinates all components"""
    
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
            
    async def scan_markets(self):
        """Scan sports markets for whale activity"""
        logger.info("Starting market scan...")
        
        # Get active sports markets from API
        async with PolymarketAPI() as api:
            markets = await api.get_sports_markets()
            
        if not markets:
            logger.warning("No markets found")
            return
            
        # Process markets with web scraping
        with PolymarketScraper(headless=True) as scraper:
            for market in markets:
                try:
                    market_url = f"{POLYMARKET_BASE_URL}/event/{market.get('slug', '')}"
                    
                    # Get whales from this market
                    whale_wallets = scraper.get_market_whales(market_url)
                    
                    # Process each whale
                    for wallet in whale_wallets:
                        # Record the sighting
                        self.db.record_whale_sighting(wallet, market_url)
                        
                        # Check if we need to update their profile
                        if self.db.needs_update(wallet):
                            profile = scraper.get_user_profile_data(wallet)
                            if profile:
                                self.db.update_bettor(profile)
                                
                    # Rate limiting between markets
                    time.sleep(2)
                    
                except Exception as e:
                    logger.error(f"Error processing market {market.get('slug', 'unknown')}: {e}")
                    continue
                    
        logger.info("Market scan completed")
        
    def check_for_alerts(self):
        """Check for new sharp bettor positions and send alerts"""
        if not self.twitter_bot:
            logger.warning("Twitter bot not configured, skipping alerts")
            return
            
        # Get new positions from sharp bettors
        new_positions = self.db.get_new_sharp_positions(hours=1)
        
        for position in new_positions:
            logger.info(f"New sharp position detected: {position['wallet_address']} in {position['market_url']}")
            
            # Send alert
            if self.twitter_bot.post_alert(position):
                logger.info("Alert posted successfully")
                
            # Rate limit Twitter posts
            time.sleep(5)
            
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
            logger.info("Starting tracker cycle...")
            
            # Scan markets for whales
            await self.scan_markets()
            
            # Check for alerts
            self.check_for_alerts()
            
            logger.info("Tracker cycle completed")
            
        except Exception as e:
            logger.error(f"Error in tracker cycle: {e}")

def main():
    """Main entry point"""
    # Load configuration from environment variables
    twitter_creds = {
        'api_key': os.getenv('TWITTER_API_KEY'),
        'api_secret': os.getenv('TWITTER_API_SECRET'),
        'access_token': os.getenv('TWITTER_ACCESS_TOKEN'),
        'access_secret': os.getenv('TWITTER_ACCESS_SECRET')
    }
    
    # Initialize tracker
    tracker = PolymarketTracker(twitter_creds)
    
    # Run initial scan
    logger.info("Running initial scan...")
    asyncio.run(tracker.run_cycle())
    
    # Schedule regular scans
    schedule.every(30).minutes.do(lambda: asyncio.run(tracker.run_cycle()))
    
    # Schedule daily leaderboard
    schedule.every().day.at("12:00").do(tracker.post_daily_leaderboard)
    
    logger.info("Polymarket tracker started. Running scans every 30 minutes...")
    
    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
