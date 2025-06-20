"""
Utility scripts for Polymarket Sharp Bettor Tracker
Useful for testing, debugging, and manual operations
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta
import sqlite3
import argparse
from polymarket_tracker import (
    PolymarketAPI, PolymarketScraper, DatabaseManager,
    BettorProfile, PolymarketTracker
)
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PolymarketUtils:
    """Utility functions for the Polymarket tracker"""
    
    def __init__(self):
        self.db = DatabaseManager()
        
    async def test_api_connection(self):
        """Test Polymarket API connectivity"""
        print("Testing Polymarket API connection...")
        
        async with PolymarketAPI() as api:
            # Test market fetch
            markets = await api.get_sports_markets('nfl')
            
            if markets:
                print(f"✓ Successfully fetched {len(markets)} NFL markets")
                print(f"Sample market: {markets[0].get('title', 'Unknown')}")
                return True
            else:
                print("✗ Failed to fetch markets")
                return False
                
    def test_scraper(self, market_slug: str = None):
        """Test web scraper functionality"""
        print("Testing web scraper...")
        
        if not market_slug:
            # Use a known active market
            market_slug = "will-the-dodgers-win-the-world-series"
            
        try:
            with PolymarketScraper(headless=False) as scraper:
                holders = scraper.get_market_top_holders(market_slug)
                
                if holders:
                    print(f"✓ Found {len(holders)} top holders")
                    for wallet, value in holders[:5]:
                        print(f"  - {wallet}: ${value:,.0f}")
                    return True
                else:
                    print("✗ No holders found")
                    return False
        except Exception as e:
            print(f"✗ Scraper error: {e}")
            return False
            
    def test_profile_scrape(self, wallet_address: str):
        """Test profile scraping for a specific wallet"""
        print(f"Testing profile scrape for {wallet_address}...")
        
        try:
            with PolymarketScraper(headless=False) as scraper:
                profile = scraper.get_user_profile_pnl(wallet_address)
                
                if profile:
                    print(f"✓ Successfully scraped profile:")
                    print(f"  - Username: {profile.username or 'N/A'}")
                    print(f"  - P&L: ${profile.total_pnl:,.0f}")
                    print(f"  - Volume: ${profile.total_volume:,.0f}")
                    print(f"  - Win Rate: {profile.win_rate:.1f}%")
                    print(f"  - ROI: {profile.roi:.1f}%")
                    return profile
                else:
                    print("✗ Failed to scrape profile")
                    return None
        except Exception as e:
            print(f"✗ Profile scrape error: {e}")
            return None
            
    def view_database_stats(self):
        """View current database statistics"""
        print("\n📊 DATABASE STATISTICS")
        print("=" * 50)
        
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            
            # Total bettors
            cursor.execute("SELECT COUNT(*) FROM bettors")
            total_bettors = cursor.fetchone()[0]
            print(f"Total bettors tracked: {total_bettors}")
            
            # Sharp bettors
            cursor.execute("SELECT COUNT(*) FROM bettors WHERE is_sharp = 1")
            sharp_bettors = cursor.fetchone()[0]
            print(f"Sharp bettors: {sharp_bettors}")
            
            # Total bets
            cursor.execute("SELECT COUNT(*) FROM bets")
            total_bets = cursor.fetchone()[0]
            print(f"Total bets tracked: {total_bets}")
            
            # Recent bets (last 24h)
            yesterday = datetime.utcnow() - timedelta(days=1)
            cursor.execute(
                "SELECT COUNT(*) FROM bets WHERE timestamp > ?",
                (yesterday,)
            )
            recent_bets = cursor.fetchone()[0]
            print(f"Bets in last 24h: {recent_bets}")
            
            # Top sharp bettors
            print("\n🏆 TOP 5 SHARP BETTORS:")
            cursor.execute("""
                SELECT wallet_address, username, total_pnl, roi, win_rate
                FROM bettors
                WHERE is_sharp = 1
                ORDER BY roi DESC
                LIMIT 5
            """)
            
            for row in cursor.fetchall():
                username = row[1] or f"{row[0][:8]}..."
                print(f"  {username}: +${row[2]:,.0f} ({row[3]:.1f}% ROI, {row[4]:.1f}% win)")
                
    def export_sharp_bettors(self, output_file: str = "sharp_bettors.json"):
        """Export sharp bettors to JSON file"""
        sharp_bettors = self.db.get_sharp_bettors()
        
        data = []
        for bettor in sharp_bettors:
            data.append({
                'wallet_address': bettor.wallet_address,
                'username': bettor.username,
                'total_pnl': bettor.total_pnl,
                'total_volume': bettor.total_volume,
                'win_rate': bettor.win_rate,
                'roi': bettor.roi,
                'last_updated': bettor.last_updated.isoformat()
            })
            
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)
            
        print(f"✓ Exported {len(data)} sharp bettors to {output_file}")
        
    def import_sharp_bettors(self, input_file: str):
        """Import sharp bettors from JSON file"""
        with open(input_file, 'r') as f:
            data = json.load(f)
            
        count = 0
        for item in data:
            bettor = BettorProfile(
                wallet_address=item['wallet_address'],
                username=item.get('username'),
                total_pnl=item['total_pnl'],
                total_volume=item['total_volume'],
                win_rate=item['win_rate'],
                roi=item['roi'],
                last_updated=datetime.fromisoformat(item['last_updated'])
            )
            self.db.update_bettor(bettor)
            count += 1
            
        print(f"✓ Imported {count} sharp bettors from {input_file}")
        
    async def manual_scan_market(self, market_slug: str):
        """Manually scan a specific market"""
        print(f"Manually scanning market: {market_slug}")
        
        # Create a tracker instance
        tracker = PolymarketTracker()
        
        # Get market info
        async with PolymarketAPI() as api:
            # This is simplified - you'd need to get the actual market data
            market = {'slug': market_slug, 'id': market_slug, 'title': market_slug}
            await tracker.analyze_market(market, 'manual')
            
        print("✓ Market scan completed")
        
    def clear_database(self, confirm: bool = False):
        """Clear all data from database (use with caution!)"""
        if not confirm:
            response = input("⚠️  This will delete all data. Type 'DELETE' to confirm: ")
            if response != 'DELETE':
                print("Cancelled.")
                return
                
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bets")
            cursor.execute("DELETE FROM bettors")
            conn.commit()
            
        print("✓ Database cleared")
        
    def generate_config_template(self):
        """Generate configuration file templates"""
        
        # .env template
        env_template = """# Polymarket Sharp Bettor Tracker Configuration

# Twitter API Credentials
TWITTER_API_KEY=your_api_key_here
TWITTER_API_SECRET=your_api_secret_here
TWITTER_ACCESS_TOKEN=your_access_token_here
TWITTER_ACCESS_SECRET=your_access_secret_here

# Feature Flags
TWITTER_ENABLED=true
HEADLESS_BROWSER=true

# Thresholds
MIN_PNL_SHARP=10000
MIN_ROI_SHARP=10
MIN_VOLUME_SHARP=50000
MIN_BET_ALERT=5000

# Scan Settings
SCAN_INTERVAL_MINUTES=30
"""
        
        with open('.env.template', 'w') as f:
            f.write(env_template)
            
        # render.yaml template
        render_template = """services:
  - type: web
    name: polymarket-tracker
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: python polymarket_tracker.py
    envVars:
      - key: TWITTER_API_KEY
        sync: false
      - key: TWITTER_API_SECRET
        sync: false
      - key: TWITTER_ACCESS_TOKEN
        sync: false
      - key: TWITTER_ACCESS_SECRET
        sync: false
"""
        
        with open('render.yaml.template', 'w') as f:
            f.write(render_template)
            
        print("✓ Generated configuration templates:")
        print("  - .env.template")
        print("  - render.yaml.template")

def main():
    parser = argparse.ArgumentParser(description='Polymarket Tracker Utilities')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Test API
    subparsers.add_parser('test-api', help='Test API connection')
    
    # Test scraper
    scraper_parser = subparsers.add_parser('test-scraper', help='Test web scraper')
    scraper_parser.add_argument('--market', type=str, help='Market slug to test')
    
    # Test profile
    profile_parser = subparsers.add_parser('test-profile', help='Test profile scraping')
    profile_parser.add_argument('wallet', type=str, help='Wallet address')
    
    # Database stats
    subparsers.add_parser('stats', help='View database statistics')
    
    # Export/Import
    export_parser = subparsers.add_parser('export', help='Export sharp bettors')
    export_parser.add_argument('--output', type=str, default='sharp_bettors.json')
    
    import_parser = subparsers.add_parser('import', help='Import sharp bettors')
    import_parser.add_argument('file', type=str, help='JSON file to import')
    
    # Manual scan
    scan_parser = subparsers.add_parser('scan', help='Manually scan a market')
    scan_parser.add_argument('market', type=str, help='Market slug')
    
    # Clear database
    clear_parser = subparsers.add_parser('clear-db', help='Clear database')
    clear_parser.add_argument('--confirm', action='store_true', help='Skip confirmation')
    
    # Generate config
    subparsers.add_parser('gen-config', help='Generate config templates')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
        
    utils = PolymarketUtils()
    
    if args.command == 'test-api':
        asyncio.run(utils.test_api_connection())
    elif args.command == 'test-scraper':
        utils.test_scraper(args.market)
    elif args.command == 'test-profile':
        utils.test_profile_scrape(args.wallet)
    elif args.command == 'stats':
        utils.view_database_stats()
    elif args.command == 'export':
        utils.export_sharp_bettors(args.output)
    elif args.command == 'import':
        utils.import_sharp_bettors(args.file)
    elif args.command == 'scan':
        asyncio.run(utils.manual_scan_market(args.market))
    elif args.command == 'clear-db':
        utils.clear_database(args.confirm)
    elif args.command == 'gen-config':
        utils.generate_config_template()

if __name__ == "__main__":
    main()
