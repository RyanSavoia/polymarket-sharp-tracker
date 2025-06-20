# Polymarket Sharp Bettor Tracker 🎯

An automated bot that tracks profitable bettors on Polymarket and sends Twitter alerts when they make significant new bets.

![Python](https://img.shields.io/badge/python-3.9+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

## 🚀 Features

- **Smart Bettor Detection**: Identifies profitable bettors with >$10k profit and >10% ROI
- **Multi-Sport Coverage**: Tracks MLB, NFL, NBA, NHL, and NCAA Basketball markets
- **Real-Time Alerts**: Twitter notifications for bets over $5,000
- **Comprehensive Analytics**: P&L tracking, win rates, and ROI calculations
- **Daily Leaderboards**: Automated posts of top profitable bettors
- **Historical Database**: SQLite storage for trend analysis

## 📋 How It Works

1. **Market Scanning**: Regularly scans sports betting markets on Polymarket
2. **Profile Analysis**: Identifies top position holders and analyzes their betting history
3. **Sharp Detection**: Flags bettors meeting profitability thresholds
4. **Alert Generation**: Sends formatted Twitter alerts for significant new positions
5. **Data Storage**: Maintains historical records for pattern analysis

## 🛠️ Tech Stack

- **Python 3.9+**: Core application
- **Selenium**: Web scraping for P&L data
- **aiohttp**: Async API requests
- **SQLite**: Local database storage
- **Tweepy**: Twitter integration
- **Schedule**: Automated task scheduling

## 📦 Installation

1. **Clone the repository**
```bash
git clone https://github.com/RyanSavoia/polymarket-sharp-tracker.git
cd polymarket-sharp-tracker
```

2. **Set up virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure environment variables**
```bash
cp .env.template .env
# Edit .env with your Twitter API credentials
```

5. **Test the setup**
```bash
python utils.py test-api
python utils.py test-scraper
```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file with:

```env
# Twitter API Credentials (required)
TWITTER_API_KEY=your_api_key
TWITTER_API_SECRET=your_api_secret
TWITTER_ACCESS_TOKEN=your_access_token
TWITTER_ACCESS_SECRET=your_access_secret

# Optional Settings
TWITTER_ENABLED=false  # Set to true when ready for production
HEADLESS_BROWSER=true  # Run Chrome in headless mode
```

### Sharp Bettor Criteria

Edit thresholds in `polymarket_tracker.py`:

```python
# Customize these values
MIN_PNL = 10000      # Minimum profit to be considered sharp
MIN_ROI = 10         # Minimum ROI percentage
MIN_VOLUME = 50000   # Minimum betting volume
MIN_BET_ALERT = 5000 # Minimum bet size for alerts
```

## 🚀 Usage

### Running Locally

```bash
# Run the main tracker
python polymarket_tracker.py

# Run utilities
python utils.py --help
```

### Utility Commands

```bash
# Test API connection
python utils.py test-api

# View database statistics
python utils.py stats

# Export sharp bettors list
python utils.py export --output sharp_bettors.json

# Manually scan a specific market
python utils.py scan "market-slug-here"

# Generate config templates
python utils.py gen-config
```

## 📊 Sample Output

### Twitter Alert Format:
```
🚨 SHARP BETTOR ALERT

CryptoWhale (0x7f2...3d4)
💰 Lifetime P&L: +$127,430
📊 ROI: 23.5%

Just bet $15,000 on:
📍 Lakers vs Celtics - Lakers to Win
🎯 YES @ $0.652

#NBA #Polymarket #SharpBettors
```

### Database Schema

**bettors** table:
- `wallet_address` (PRIMARY KEY)
- `username`
- `total_pnl`
- `total_volume`
- `win_rate`
- `roi`
- `is_sharp`

**bets** table:
- `id` (PRIMARY KEY)
- `bettor_address`
- `market_id`
- `market_title`
- `outcome`
- `amount`
- `price`
- `timestamp`

## 🚢 Deployment

### Deploy to Render

1. Push code to GitHub
2. Create new Web Service on [Render](https://render.com)
3. Connect GitHub repository
4. Configure:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python polymarket_tracker.py`
5. Add environment variables in dashboard

### Deploy with Docker

```dockerfile
FROM python:3.9-slim
# See Dockerfile in repo for full configuration
```

## 🔧 Development

### Project Structure
```
polymarket-sharp-tracker/
├── polymarket_tracker.py  # Main bot application
├── utils.py              # Utility scripts
├── requirements.txt      # Python dependencies
├── .env.template        # Environment variable template
├── .gitignore          # Git ignore rules
├── README.md           # This file
└── LICENSE            # MIT license
```

### Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## ⚠️ Disclaimers

- **Educational Purpose**: This tool is for educational and research purposes
- **API Compliance**: Respects Polymarket's public data access policies
- **Rate Limiting**: Implements delays to avoid overwhelming servers
- **No Financial Advice**: Not intended as financial or betting advice
- **User Responsibility**: Users must comply with local laws and regulations

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Support

- **Issues**: [GitHub Issues](https://github.com/RyanSavoia/polymarket-sharp-tracker/issues)
- **Discussions**: [GitHub Discussions](https://github.com/RyanSavoia/polymarket-sharp-tracker/discussions)

## 🔮 Future Enhancements

- [ ] Machine learning for bet prediction
- [ ] Discord/Telegram integration
- [ ] Web dashboard interface
- [ ] Cross-market arbitrage detection
- [ ] Advanced pattern recognition
- [ ] API-only mode (no scraping)

---

**Note**: Remember to star ⭐ this repo if you find it useful!
