"""
Polymarket Sharp Bettor Tracker Bot

This bot tracks profitable bettors on Polymarket and alerts when they make new bets.
It combines API access with web scraping to gather comprehensive betting data.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
POLYMARKET_API_BASE = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_API = "https://clob.polymarket.com"
POLYMARKET_BASE_URL = "https://polymarket.com"
TWITTER_ENABLED = True  # Set to False to disable Twitter posting during testing

@dataclass
class BettorProfile:
    """Data class for bettor profile information"""
    wallet_address: str
    username: Optional[str]
    total_pnl: float
    total_volume: float
    win_rate: float
    roi: float
    last_updated: datetime
    
@dataclass
class Bet:
    """Data class for individual bet information"""
    bettor_address: str
    market_id: str
    market_title: str
    outcome: str
    amount: float
    price: float
    timestamp: datetime
    market_category: str

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
            
    async def get_sports_markets(self, sport: str) -> List[Dict]:
        """Fetch all markets for a specific sport"""
        try:
            url = f"{POLYMARKET_API_BASE}/markets"
            params = {
                "group": sport.upper(),
                "active": "true",
                "closed": "false",
                "limit": 100
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"Failed to fetch {sport} markets: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching sports markets: {e}")
            return []
            
    async def get_market_positions(self, market_id: str) -> List[Dict]:
        """Get top position holders for a specific market"""
        try:
            # Using the CLOB API to get order book data
            url = f"{POLYMARKET_CLOB_API}/book"
            params = {"token_id": market_id}
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"Failed to fetch market positions: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching market positions: {e}")
            return []
            
    async def get_user_positions(self, wallet_address: str) -> List[Dict]:
        """Get all positions for a specific user"""
        try:
            url = f"{POLYMARKET_API_BASE}/positions"
            params = {
                "user": wallet_address,
                "minSize": 100,  # Only track significant positions
                "limit": 50
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"Failed to fetch user positions: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching user positions: {e}")
            return []

class PolymarketScraper:
    """Handles web scraping for data not available via API"""
    
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
        
        self.driver = webdriver.Chrome(options=options)
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            self.driver.quit()
            
    def get_market_top_holders(self, market_slug: str) -> List[Tuple[str, float]]:
        """Scrape top holders for a specific market"""
        try:
            url = f"{POLYMARKET_BASE_URL}/event/{market_slug}"
            self.driver.get(url)
            
            # Wait for the page to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "c-positions-table"))
            )
            
            holders = []
            
            # Look for top holders section
            holders_section = self.driver.find_elements(By.CSS_SELECTOR, ".c-positions-table-row")
            
            for holder in holders_section[:20]:  # Top 20 holders
                try:
                    wallet = holder.find_element(By.CSS_SELECTOR, ".c-positions-table-user").text
                    position_value = holder.find_element(By.CSS_SELECTOR, ".c-positions-table-value").text
                    
                    # Clean up the position value (remove $ and commas)
                    position_value = float(position_value.replace('$', '').replace(',', ''))
                    
                    if position_value >= 1000:  # Only track significant positions
                        holders.append((wallet, position_value))
                except Exception as e:
                    logger.debug(f"Error parsing holder data: {e}")
                    continue
                    
            return holders
            
        except Exception as e:
            logger.error(f"Error scraping market holders: {e}")
            return []
            
    def get_user_profile_pnl(self, wallet_address: str) -> Optional[BettorProfile]:
        """Scrape user profile to get P&L data"""
        try:
            url = f"{POLYMARKET_BASE_URL}/profile/{wallet_address}"
            self.driver.get(url)
            
            # Wait for profile to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "c-profile-stats"))
            )
            
            # Extract profile stats
            stats = {}
            stat_elements = self.driver.find_elements(By.CSS_SELECTOR, ".c-profile-stat")
            
            for stat in stat_elements:
                try:
                    label = stat.find_element(By.CSS_SELECTOR, ".c-profile-stat-label").text.lower()
                    value = stat.find_element(By.CSS_SELECTOR, ".c-profile-stat-value").text
                    
                    if "p&l" in label or "profit" in label:
                        stats['pnl'] = float(value.replace('$', '').replace(',', '').replace('+', ''))
                    elif "volume" in label:
                        stats['volume'] = float(value.replace('$', '').replace(',', ''))
                    elif "markets" in label:
                        stats['markets'] = int(value.replace(',', ''))
                    elif "win rate" in label:
                        stats['win_rate'] = float(value.replace('%', ''))
                except Exception as e:
                    logger.debug(f"Error parsing stat: {e}")
                    continue
                    
            # Calculate ROI if we have the data
            roi = 0
            if stats.get('volume', 0) > 0:
                roi = (stats.get('pnl', 0) / stats.get('volume', 1)) * 100
                
            # Get username if available
            username = None
            try:
                username_elem = self.driver.find_element(By.CSS_SELECTOR, ".c-profile-username")
                username = username_elem.text
            except:
                pass
                
            return BettorProfile(
                wallet_address=wallet_address,
                username=username,
                total_pnl=stats.get('pnl', 0),
                total_volume=stats.get('volume', 0),
                win_rate=stats.get('win_rate', 0),
                roi=roi,
                last_updated=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error scraping user profile: {e}")
            return None

class DatabaseManager:
    """Manages SQLite database for storing bettor and bet data"""
    
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
                    win_rate REAL,
                    roi REAL,
                    last_updated TIMESTAMP,
                    is_sharp BOOLEAN DEFAULT 0
                )
            """)
            
            # Bets table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bettor_address TEXT,
                    market_id TEXT,
                    market_title TEXT,
                    outcome TEXT,
                    amount REAL,
                    price REAL,
                    timestamp TIMESTAMP,
                    market_category TEXT,
                    alert_sent BOOLEAN DEFAULT 0,
                    FOREIGN KEY (bettor_address) REFERENCES bettors (wallet_address)
                )
            """)
            
            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bets_timestamp ON bets(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bets_bettor ON bets(bettor_address)")
            
            conn.commit()
            
    def update_bettor(self, bettor: BettorProfile):
        """Update or insert bettor profile"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Check if bettor qualifies as sharp
            is_sharp = (
                bettor.total_pnl > 10000 and  # Profitable by at least $10k
                bettor.roi > 10 and  # ROI > 10%
                bettor.total_volume > 50000  # Significant volume
            )
            
            cursor.execute("""
                INSERT OR REPLACE INTO bettors 
                (wallet_address, username, total_pnl, total_volume, win_rate, roi, last_updated, is_sharp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bettor.wallet_address, bettor.username, bettor.total_pnl,
                bettor.total_volume, bettor.win_rate, bettor.roi,
                bettor.last_updated, is_sharp
            ))
            
            conn.commit()
            
    def add_bet(self, bet: Bet):
        """Add new bet to database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Check if bet already exists
            cursor.execute("""
                SELECT id FROM bets 
                WHERE bettor_address = ? AND market_id = ? 
                AND ABS(amount - ?) < 1 AND ABS(timestamp - ?) < 60
            """, (bet.bettor_address, bet.market_id, bet.amount, bet.timestamp.timestamp()))
            
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO bets 
                    (bettor_address, market_id, market_title, outcome, amount, price, timestamp, market_category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    bet.bettor_address, bet.market_id, bet.market_title,
                    bet.outcome, bet.amount, bet.price, bet.timestamp, bet.market_category
                ))
                
                conn.commit()
                return cursor.lastrowid
            return None
            
    def get_sharp_bettors(self) -> List[BettorProfile]:
        """Get all sharp bettors from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM bettors 
                WHERE is_sharp = 1 
                ORDER BY roi DESC
            """)
            
            rows = cursor.fetchall()
            bettors = []
            
            for row in rows:
                bettors.append(BettorProfile(
                    wallet_address=row[0],
                    username=row[1],
                    total_pnl=row[2],
                    total_volume=row[3],
                    win_rate=row[4],
                    roi=row[5],
                    last_updated=datetime.fromisoformat(row[6])
                ))
                
            return bettors
            
    def get_recent_unalerted_bets(self) -> List[Dict]:
        """Get recent bets that haven't been alerted yet"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT b.*, bt.username, bt.total_pnl, bt.roi 
                FROM bets b
                JOIN bettors bt ON b.bettor_address = bt.wallet_address
                WHERE b.alert_sent = 0 AND bt.is_sharp = 1
                AND b.amount >= 5000  -- Only alert for significant bets
                ORDER BY b.timestamp DESC
            """)
            
            rows = cursor.fetchall()
            bets = []
            
            for row in rows:
                bets.append({
                    'id': row[0],
                    'bettor_address': row[1],
                    'market_id': row[2],
                    'market_title': row[3],
                    'outcome': row[4],
                    'amount': row[5],
                    'price': row[6],
                    'timestamp': row[7],
                    'market_category': row[8],
                    'username': row[10],
                    'bettor_pnl': row[11],
                    'bettor_roi': row[12]
                })
                
            return bets
            
    def mark_bet_alerted(self, bet_id: int):
        """Mark a bet as having been alerted"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE bets SET alert_sent = 1 WHERE id = ?", (bet_id,))
            conn.commit()

class TwitterBot:
    """Handles Twitter/X posting functionality"""
    
    def __init__(self, api_key: str, api_secret: str, access_token: str, access_secret: str):
        auth = tweepy.OAuthHandler(api_key, api_secret)
        auth.set_access_token(access_token, access_secret)
        self.api = tweepy.API(auth)
        
    def post_alert(self, bet_data: Dict) -> bool:
        """Post a bet alert to Twitter"""
        try:
            # Format the alert message
            username = bet_data.get('username', 'Unknown')
            wallet_short = f"{bet_data['bettor_address'][:6]}...{bet_data['bettor_address'][-4:]}"
            
            message = f"""🚨 SHARP BETTOR ALERT

{username} ({wallet_short})
💰 Lifetime P&L: +${bet_data['bettor_pnl']:,.0f}
📊 ROI: {bet_data['bettor_roi']:.1f}%

Just bet ${bet_data['amount']:,.0f} on:
📍 {bet_data['market_title']}
🎯 {bet_data['outcome']} @ ${bet_data['price']:.3f}

#{bet_data['market_category']} #Polymarket #SharpBettors"""

            if TWITTER_ENABLED:
                self.api.update_status(message)
                logger.info(f"Posted alert for bet ID: {bet_data['id']}")
                return True
            else:
                logger.info(f"Twitter disabled - would have posted: {message}")
                return True
                
        except Exception as e:
            logger.error(f"Error posting to Twitter: {e}")
            return False

class PolymarketTracker:
    """Main tracker class that coordinates all components"""
    
    def __init__(self, twitter_credentials: Optional[Dict] = None):
        self.db = DatabaseManager()
        self.twitter_bot = None
        
        if twitter_credentials and TWITTER_ENABLED:
            self.twitter_bot = TwitterBot(
                twitter_credentials['api_key'],
                twitter_credentials['api_secret'],
                twitter_credentials['access_token'],
                twitter_credentials['access_secret']
            )
            
        self.sports = ['mlb', 'nfl', 'nba', 'nhl', 'ncaa-basketball']
        
    async def scan_sports_markets(self):
        """Scan all sports markets for betting activity"""
        logger.info("Starting sports market scan...")
        
        async with PolymarketAPI() as api:
            for sport in self.sports:
                logger.info(f"Scanning {sport} markets...")
                markets = await api.get_sports_markets(sport)
                
                for market in markets:
                    # Get top holders using web scraping
                    await self.analyze_market(market, sport)
                    
                # Rate limiting
                await asyncio.sleep(2)
                
    async def analyze_market(self, market: Dict, sport: str):
        """Analyze a specific market for sharp bettor activity"""
        try:
            market_id = market.get('id')
            market_slug = market.get('slug')
            market_title = market.get('title', 'Unknown Market')
            
            if not market_slug:
                return
                
            # Use web scraper to get top holders
            with PolymarketScraper() as scraper:
                top_holders = scraper.get_market_top_holders(market_slug)
                
                for wallet, position_value in top_holders:
                    # Check if we need to update this bettor's profile
                    await self.update_bettor_profile(wallet)
                    
                    # Check for new bets
                    await self.check_for_new_bets(wallet, market_id, market_title, sport, position_value)
                    
        except Exception as e:
            logger.error(f"Error analyzing market {market.get('title', 'Unknown')}: {e}")
            
    async def update_bettor_profile(self, wallet_address: str):
        """Update bettor profile if needed"""
        try:
            # Check if we need to update (last update > 6 hours ago)
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT last_updated FROM bettors 
                    WHERE wallet_address = ?
                """, (wallet_address,))
                
                result = cursor.fetchone()
                
                if result:
                    last_updated = datetime.fromisoformat(result[0])
                    if datetime.utcnow() - last_updated < timedelta(hours=6):
                        return  # Skip update if recent
                        
            # Scrape profile data
            with PolymarketScraper() as scraper:
                profile = scraper.get_user_profile_pnl(wallet_address)
                
                if profile:
                    self.db.update_bettor(profile)
                    logger.info(f"Updated profile for {wallet_address}: P&L=${profile.total_pnl:,.0f}, ROI={profile.roi:.1f}%")
                    
        except Exception as e:
            logger.error(f"Error updating bettor profile: {e}")
            
    async def check_for_new_bets(self, wallet_address: str, market_id: str, 
                                market_title: str, sport: str, position_value: float):
        """Check if this represents a new bet from a sharp bettor"""
        try:
            # Get current positions for this user in this market
            async with PolymarketAPI() as api:
                positions = await api.get_user_positions(wallet_address)
                
                # Find position for this specific market
                for position in positions:
                    if position.get('market', {}).get('id') == market_id:
                        # This is a simplified check - in production you'd want more sophisticated logic
                        # to detect NEW bets vs existing positions
                        
                        bet = Bet(
                            bettor_address=wallet_address,
                            market_id=market_id,
                            market_title=market_title,
                            outcome=position.get('outcome', 'Unknown'),
                            amount=position_value,
                            price=position.get('price', 0),
                            timestamp=datetime.utcnow(),
                            market_category=sport
                        )
                        
                        bet_id = self.db.add_bet(bet)
                        if bet_id:
                            logger.info(f"New bet detected: {wallet_address} bet ${position_value:,.0f} on {market_title}")
                            
        except Exception as e:
            logger.error(f"Error checking for new bets: {e}")
            
    def send_alerts(self):
        """Send alerts for new sharp bettor activity"""
        if not self.twitter_bot:
            logger.warning("Twitter bot not configured, skipping alerts")
            return
            
        # Get unalerted bets
        bets = self.db.get_recent_unalerted_bets()
        
        for bet in bets:
            if self.twitter_bot.post_alert(bet):
                self.db.mark_bet_alerted(bet['id'])
                
            # Rate limit Twitter posts
            time.sleep(5)
            
    def generate_leaderboard(self) -> str:
        """Generate a leaderboard of top sharp bettors"""
        sharp_bettors = self.db.get_sharp_bettors()
        
        leaderboard = "🏆 TOP SHARP BETTORS ON POLYMARKET\n\n"
        
        for i, bettor in enumerate(sharp_bettors[:10], 1):
            username = bettor.username or f"{bettor.wallet_address[:8]}..."
            leaderboard += f"{i}. {username}\n"
            leaderboard += f"   💰 P&L: +${bettor.total_pnl:,.0f}\n"
            leaderboard += f"   📊 ROI: {bettor.roi:.1f}%\n"
            leaderboard += f"   🎯 Win Rate: {bettor.win_rate:.1f}%\n\n"
            
        return leaderboard
        
    async def run_scan_cycle(self):
        """Run a complete scan cycle"""
        try:
            logger.info("Starting scan cycle...")
            await self.scan_sports_markets()
            self.send_alerts()
            logger.info("Scan cycle completed")
        except Exception as e:
            logger.error(f"Error in scan cycle: {e}")

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
    asyncio.run(tracker.run_scan_cycle())
    
    # Schedule regular scans (every 30 minutes)
    schedule.every(30).minutes.do(lambda: asyncio.run(tracker.run_scan_cycle()))
    
    # Schedule daily leaderboard post
    schedule.every().day.at("12:00").do(
        lambda: tracker.twitter_bot.post_alert({'message': tracker.generate_leaderboard()})
    )
    
    logger.info("Polymarket tracker started. Running scans every 30 minutes...")
    
    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
