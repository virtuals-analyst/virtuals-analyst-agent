import time
import hashlib
import logging
from datetime import datetime
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import openai
from openai import OpenAI
import os

from dotenv import load_dotenv
load_dotenv()

# Define the URL to scrape
URL = "https://fun.virtuals.io"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('virtuals.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Add these near the top of the file with other imports
client = OpenAI(
    api_key=os.getenv('OPENAI_API_KEY')  # Make sure to set your API key as an environment variable
)

def setup_driver():
    """Set up headless Chrome driver"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=chrome_options)

def fetch_page_content(driver, url):
    """Fetch the content of the webpage using Selenium"""
    try:
        print("Fetching page...")
        driver.get(url)
        
        # Initial wait for page load
        print("Waiting for initial page load...")
        time.sleep(5)
        
        # Wait for skeleton to disappear and check content
        max_retries = 3
        for attempt in range(max_retries):
            html_content = driver.page_source
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Check for agent cards
            agent_cards = soup.find_all("a", class_="w-full", href=lambda x: x and x.startswith("/agents/"))
            
            if agent_cards:
                logger.info(f"Found {len(agent_cards)} agent cards after attempt {attempt + 1}")
                return html_content
            
            logger.info(f"No agent cards found on attempt {attempt + 1}, waiting longer...")
            time.sleep(5)  # Wait between attempts
        
        logger.warning("Failed to find agent cards after all attempts")
        return None
        
    except Exception as e:
        logger.error(f"Error fetching the URL: {e}")
        return None

def parse_agent_data(agent_div):
    """Parse individual agent data from a div element"""
    try:
        # Find the inner div that contains all the info
        inner_div = agent_div.find("div", class_="flex flex-col w-full")
        if not inner_div:
            logger.warning("Could not find inner div with class 'flex flex-col w-full'")
            return None

        try:
            # Extract name and symbol with flexible matching
            token_div = inner_div.find("div", class_=lambda x: "text-white" in str(x) and "bg-[#44BCC3]" in str(x))
            if not token_div:
                logger.warning("Could not find token div")
                return None
                
            name_elem = token_div.find("p", class_=lambda x: "text-white" in str(x) and "text-lg" in str(x))
            symbol_elem = token_div.find("p", class_=lambda x: "text-white/50" in str(x))
            
            if not name_elem or not symbol_elem:
                logger.warning(f"Missing name or symbol elements. Name: {bool(name_elem)}, Symbol: {bool(symbol_elem)}")
                return None
                
            name = name_elem.text.strip()
            symbol = symbol_elem.text.strip()
            symbol = symbol.replace("(", "").replace(")", "").strip()
            
            # Extract market cap with flexible matching
            market_cap_p = inner_div.find("p", class_=lambda x: "text-[#00FFA3]" in str(x))
            if not market_cap_p:
                logger.warning("Could not find market cap paragraph")
                return None
                
            market_cap_span = market_cap_p.find("span", class_=lambda x: "text-lg" in str(x))
            if not market_cap_span:
                logger.warning("Could not find market cap span")
                return None
                
            market_cap = market_cap_span.text.strip()
            
            # Extract creator info with flexible matching
            creator_link = inner_div.find("a", href=lambda x: x and "/profile/" in str(x))
            creator_p = creator_link.find("p", class_=lambda x: "text-[#FCE94B]" in str(x) and "text-lg" in str(x)) if creator_link else None
            creator = creator_p.text.strip() if creator_p else "Unknown"
            
            # Extract time with flexible matching
            time_elements = inner_div.find_all("p", class_=lambda x: "text-[#FCE94B]" in str(x) and "text-sm" in str(x))
            time_text = time_elements[-1].text.strip() if time_elements else "Unknown"
            
            # Extract description with flexible matching
            desc_elem = inner_div.find("p", class_=lambda x: "text-[#A0CFCB]" in str(x))
            description = desc_elem.text.strip() if desc_elem else "No description available"

            agent_data = {
                "name": name,
                "symbol": symbol,
                "market_cap": market_cap,
                "creator": creator,
                "time": time_text,
                "description": description
            }
            
            logger.debug(f"Successfully parsed agent data: {agent_data}")
            return agent_data
            
        except Exception as e:
            logger.error(f"Error parsing agent elements: {e}")
            return None

    except Exception as e:
        logger.error(f"Error in parse_agent_data: {e}")
        return None

def parse_and_find_updates(html_content):
    """Parse the HTML content and extract agent information"""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Find all agent cards directly
        agent_cards = soup.find_all("a", class_="w-full", href=lambda x: x and x.startswith("/agents/"))
        
        if not agent_cards:
            logger.warning("No agent cards found in parsed content")
            return {}
            
        agents = {}
        for card in agent_cards:
            try:
                # Find the main div with flexible matching
                agent_div = card.find("div", class_=lambda x: "w-full" in str(x) and "flex" in str(x) and "gap-2" in str(x))
                if agent_div:
                    agent_data = parse_agent_data(agent_div)
                    if agent_data:
                        agents[agent_data["name"]] = agent_data
                        logger.debug(f"Successfully parsed agent: {agent_data['name']}")
                else:
                    logger.warning("Could not find main div in card")
            except Exception as e:
                logger.error(f"Error parsing individual card: {e}")
                continue
        
        logger.info(f"Successfully found and parsed {len(agents)} agents")
        return agents
        
    except Exception as e:
        logger.error(f"Error parsing HTML content: {e}")
        return {}

def detect_agent_changes(current_agents, previous_agents):
    """Detect changes in agent data between current and previous state"""
    changes = {
        "new": [],
        "updated": [],
        "removed": []
    }
    
    # Find new and updated agents
    for name, data in current_agents.items():
        if name not in previous_agents:
            changes["new"].append(data)
        elif previous_agents[name] != data:
            changes["updated"].append(data)
    
    # Find removed agents
    for name in previous_agents:
        if name not in current_agents:
            changes["removed"].append(name)
    
    return changes

def get_token_age_minutes(time_str):
    """Convert time string to minutes"""
    try:
        time_str = time_str.lower().strip()
        logger.debug(f"Converting time string: {time_str}")
        
        # Handle special cases first
        if time_str == 'a minute ago':
            logger.debug("Parsed: a minute ago -> 1 minute")
            return 1
        elif time_str == 'an hour ago':
            logger.debug("Parsed: an hour ago -> 60 minutes")
            return 60
        elif time_str == 'a day ago':
            logger.debug("Parsed: a day ago -> 1440 minutes")
            return 1440
        
        # Handle regular cases
        if 'minute ago' in time_str or 'minutes ago' in time_str:
            minutes = int(time_str.split()[0])
            logger.debug(f"Parsed minutes: {minutes}")
            return minutes
            
        elif 'hour ago' in time_str or 'hours ago' in time_str:
            hours = int(time_str.split()[0])
            minutes = hours * 60
            logger.debug(f"Parsed hours: {hours} -> {minutes} minutes")
            return minutes
            
        elif 'day' in time_str or 'days' in time_str:
            days = int(time_str.split()[0])
            minutes = days * 1440  # 24 hours * 60 minutes
            logger.debug(f"Parsed days: {days} -> {minutes} minutes")
            return minutes
            
        else:
            logger.debug(f"Unknown time format: {time_str}, returning max age")
            return 1440  # Default to 24 hours for unknown formats
            
    except Exception as e:
        logger.error(f"Error parsing time string '{time_str}': {e}")
        return 1440  # Return 24 hours on error

def analyze_agent_with_ai(agent_data):
    """Use OpenAI to analyze an agent and provide a human-readable explanation"""
    try:
        # Convert market cap to numeric value
        market_cap_value = float(agent_data['market_cap'].replace('k', '000'))
        age_minutes = get_token_age_minutes(agent_data['time'])
        
        # Determine rating based on exact rules
        if market_cap_value >= 10000 and age_minutes <= 10:
            rating = '🔥'  # Hot new token with high market cap
        elif market_cap_value >= 7000:  # This check must come BEFORE the dead token check
            rating = '👍'  # Good market cap regardless of age
        elif market_cap_value < 5000 and age_minutes > 10:
            rating = '💀'  # Dead token
        elif 5000 <= market_cap_value < 7000:
            if age_minutes <= 10:
                rating = '🆙'  # New token with decent market cap
            else:
                rating = '👎'  # Older token with decent market cap
        else:
            rating = '👎'  # Default case

        # Debug logging
        logger.debug(f"""
        Token Rating Calculation:
        Name: {agent_data['name']}
        Market Cap: {market_cap_value:,.0f}
        Age (minutes): {age_minutes}
        Assigned Rating: {rating}
        """)

        prompt = f"""
        Analyze this virtuals.io AI agent token and provide a brief, clear explanation:
        
        Name: {agent_data['name']}
        Symbol: {agent_data['symbol']}
        Market Cap: {agent_data['market_cap']} ({market_cap_value:,.0f})
        Age: {age_minutes} minutes
        Creator: {agent_data['creator']}
        Time Created: {agent_data['time']}
        Description: {agent_data['description']}
        
        Rating Rules Applied:
        - Market Cap: {market_cap_value:,.0f}
        - Age: {age_minutes} minutes
        - Assigned Rating: {rating}
        
        You MUST use this exact rating in your analysis: {rating}
        
        Format your response with the rating at the start:
        {rating}

        1. Risk: (brief risk assessment)
        2. Potential: (growth potential analysis)
        3. Verdict: (final recommendation including the exact rating emoji: {rating})
        """

        max_retries = 3
        for attempt in range(max_retries):
            response = client.chat.completions.create(
                model="gpt-3.5-turbo-0125",
                messages=[
                    {"role": "system", "content": f"You are a crypto analyst. You MUST use exactly this rating emoji in your analysis: {rating}"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=250,
                temperature=0.7
            )
            
            analysis = response.choices[0].message.content
            
            # Verify that the analysis contains the correct rating emoji
            if rating in analysis:
                logger.debug(f"AI Analysis generated for {agent_data['name']} with rating {rating}")
                return analysis
            
            logger.warning(f"Analysis missing correct rating emoji, attempt {attempt + 1} of {max_retries}")
        
        # If we get here, all attempts failed to include the emoji
        return f"{rating}\n\nRating: {rating}\n{analysis}"
        
    except Exception as e:
        logger.error(f"Error generating AI analysis: {e}")
        return f"{rating} Analysis unavailable"

def analyze_token_page(driver, token_url, token_data):
    """Fetch and analyze the detailed token page"""
    try:
        logger.info(f"Analyzing token page for {token_data['name']}")
        
        # Construct full URL
        full_url = f"https://fun.virtuals.io{token_url}"
        driver.get(full_url)
        
        # Wait for chat container to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "overflow-y-auto"))
        )
        time.sleep(3)  # Additional wait for content
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        token_details = {
            "holders": "Unknown",
            "total_supply": "Unknown",
            "contract_address": "Unknown",
            "social_links": [],
            "chat_activity": {
                "messages": [],
                "notifications": [],
                "engagement_score": 0,
                "last_activity": None,
                "total_messages": 0
            }
        }
        
        try:
            # Find chat/activity feed
            chat_container = soup.find("div", class_=lambda x: "max-h-[60vh]" in str(x) and "overflow-y-auto" in str(x))
            if chat_container:
                messages = []
                notifications = []
                
                # Process all message divs
                chat_items = chat_container.find_all("div", class_=lambda x: "flex flex-row items-center gap-1" in str(x))
                
                for item in chat_items:
                    try:
                        # Determine if notification or message
                        is_notification = bool(item.find("p", class_=lambda x: x and "bg-[#DFF5BA]" in str(x)))
                        
                        # Get timestamp
                        timestamp = item.find("p", class_=lambda x: "text-white/50" in str(x) and "text-nowrap" in str(x))
                        timestamp_text = timestamp.text.strip() if timestamp else "Unknown"
                        
                        # Get message content
                        if is_notification:
                            content = item.find("p", class_=lambda x: "text-[#A0CFCB]" in str(x))
                            notifications.append({
                                "timestamp": timestamp_text,
                                "content": content.text.strip() if content else "Unknown",
                                "type": "NOTIFICATION"
                            })
                        else:
                            content = item.find("p", class_=lambda x: "text-white" in str(x) or "text-base" in str(x))
                            author = item.find("p", class_=lambda x: x and "bg-white rounded" in str(x))
                            messages.append({
                                "timestamp": timestamp_text,
                                "content": content.text.strip() if content else "Unknown",
                                "author": author.text.strip() if author else "Unknown",
                                "type": "MESSAGE"
                            })
                    
                    except Exception as e:
                        logger.error(f"Error parsing chat item: {e}")
                        continue
                
                # Calculate engagement score
                engagement_score = len(messages) + (len(notifications) * 2)  # Notifications count more
                
                token_details["chat_activity"] = {
                    "messages": messages,
                    "notifications": notifications,
                    "engagement_score": engagement_score,
                    "last_activity": messages[0]["timestamp"] if messages else None,
                    "total_messages": len(messages) + len(notifications)
                }
            
            # Generate chat analysis prompt
            chat_analysis_prompt = f"""
            Analyze the chat activity for {token_data['name']} token:
            
            Total Messages: {token_details['chat_activity']['total_messages']}
            Engagement Score: {token_details['chat_activity']['engagement_score']}
            Last Activity: {token_details['chat_activity']['last_activity']}
            
            Recent Notifications:
            {chr(10).join([f"- {n['timestamp']}: {n['content']}" for n in token_details['chat_activity']['notifications'][:3]])}
            
            Recent Messages:
            {chr(10).join([f"- {m['author']}: {m['content']}" for m in token_details['chat_activity']['messages'][:3]])}
            
            Please provide:
            1. Community Activity Assessment
            2. Engagement Quality Analysis
            3. Recent Updates/Announcements Review
            4. Red Flags or Positive Indicators
            5. Overall Community Health Score (0-10)
            """
            
            # Get chat analysis
            chat_analysis = analyze_agent_with_ai({
                "name": token_data['name'],
                "symbol": token_data['symbol'],
                "market_cap": token_data['market_cap'],
                "description": chat_analysis_prompt
            })
            
            token_details["chat_analysis"] = chat_analysis
            
        except Exception as e:
            logger.error(f"Error analyzing chat activity: {e}")
        
        return token_details
        
    except Exception as e:
        logger.error(f"Error analyzing token page: {e}")
        return None

def log_changes(changes):
    """Log detected changes to both log file and updates_log.txt"""
    if not any(changes.values()):
        return

    timestamp = datetime.now().isoformat()
    driver = setup_driver()
    
    try:
        with open("updates_log.txt", "a") as log_file:
            log_file.write(f"\n=== Changes detected at {timestamp} ===\n")
            
            if changes["new"]:
                log_file.write("\nNew Agents:\n")
                for agent in changes["new"]:
                    # Get initial AI analysis
                    analysis = analyze_agent_with_ai(agent)
                    
                    # If token looks promising, get detailed analysis
                    if "👍" in analysis:
                        log_file.write(f"\n🔍 DETAILED ANALYSIS FOR PROMISING TOKEN: {agent['name']}\n")
                        print(f"\n🔍 Getting detailed analysis for promising token: {agent['name']}")
                        
                        token_url = f"/agents/{agent.get('contract_address', '')}"
                        detailed_info = analyze_token_page(driver, token_url, agent)
                        
                        if detailed_info:
                            log_file.write("\nDetailed Token Information:\n")
                            log_file.write(f"Holders: {detailed_info['token_details']['holders']}\n")
                            log_file.write(f"Social Links: {', '.join(detailed_info['token_details']['social_links'])}\n")
                            log_file.write("\nDetailed Analysis:\n")
                            log_file.write(f"{detailed_info['detailed_analysis']}\n")
                            
                            # Print to console as well
                            print(f"\n🔍 Detailed Analysis for {agent['name']}:")
                            print(detailed_info['detailed_analysis'])
                    
                    # Log basic information
                    log_file.write(f"\n- {agent['name']} ({agent['symbol']})\n")
                    log_file.write(f"  Market Cap: {agent['market_cap']}\n")
                    log_file.write(f"  Creator: {agent['creator']}\n")
                    log_file.write(f"  Time: {agent['time']}\n")
                    log_file.write(f"  Description: {agent['description']}\n")
                    log_file.write(f"  Initial Analysis:\n{analysis}\n")
                    log_file.write("-" * 50 + "\n")
            
            if changes["updated"]:
                log_file.write("\nUpdated Agents:\n")
                for agent in changes["updated"]:
                    log_file.write(f"- {agent['name']} ({agent['symbol']})\n")
                    log_file.write(f"  Market Cap: {agent['market_cap']}\n")
                    log_file.write(f"  Time: {agent['time']}\n")
            
            if changes["removed"]:
                log_file.write("\nRemoved Agents:\n")
                for name in changes["removed"]:
                    log_file.write(f"- {name}\n")
    
    finally:
        driver.quit()

def display_all_agents(agents, num_recent=50):
    """Display only the most recent agents"""
    logger.info(f"\nCurrently listed agents ({len(agents)}) - Showing {num_recent} most recent")
    
    # Sort agents by time (most recent first)
    sorted_agents = sorted(
        agents.values(),
        key=lambda x: x['time'],  # Sort by time
        reverse=True  # Most recent first
    )[:num_recent]  # Take only the N most recent
    
    # Print to console
    for agent in sorted_agents:
        print("\n" + "="*50)
        print(f"Name: {agent['name']} ({agent['symbol']})")
        print(f"Market Cap: {agent['market_cap']}")
        print(f"Creator: {agent['creator']}")
        print(f"Time: {agent['time']}")
        print(f"Description: {agent['description']}")
        
        # Add AI analysis
        analysis = analyze_agent_with_ai(agent)
        print("\nAI Analysis:")
        print(analysis)
    
    print("\n" + "="*50)
    logger.info("Initial agent listing complete. Starting change monitoring...")

def validate_market_cap(market_cap_str):
    """Validate and convert market cap string to numeric value"""
    try:
        logger.info(f"💰 Validating Market Cap: {market_cap_str}")
        
        # Clean the input
        clean_str = market_cap_str.strip().lower()
        logger.debug(f"Cleaned string: '{clean_str}'")
        
        # Handle 'k' suffix
        if clean_str.endswith('k'):
            base_value = float(clean_str[:-1])
            result = base_value * 1000
            logger.debug(f"K conversion: {base_value}k -> ${result:,.0f}")
        else:
            result = float(clean_str)
            logger.debug(f"Direct conversion: ${result:,.0f}")
            
        # Sanity check
        if result < 100:  # Suspiciously low
            logger.warning(f"⚠️ Very low market cap value: ${result:,.0f}")
            # Try to fix common conversion errors
            if clean_str.endswith('k'):
                corrected = float(clean_str[:-1]) * 1000
                logger.info(f"🔄 Attempting correction: {clean_str} -> ${corrected:,.0f}")
                result = corrected
        
        logger.debug(f"✅ Final market cap value: ${result:,.0f}")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error validating market cap '{market_cap_str}': {e}")
        # Return a default value instead of 0 to avoid division issues
        return 1000.0  # Default to 1k market cap on error

def get_token_rating(market_cap_str, time_str):
    """Determine token rating based on market cap and age"""
    try:
        logger.info(f"\n=== Starting Rating Calculation for {market_cap_str} ===")
        
        # Get exact market cap value
        market_cap_value = validate_market_cap(market_cap_str)
        age_minutes = get_token_age_minutes(time_str)
        
        # Detailed decision logging
        logger.info(f"""
Rating Criteria Check:
Market Cap: ${market_cap_value:,.0f}
Age: {age_minutes} minutes
Thresholds:
- 🔥 requires: >= $100,000 OR (>= $10,000 and <= 10 mins old)
- 👍 requires: >= $7,000
- 💀 requires: < $5,000 and > 10 mins old
- 🆙 requires: $5,000-$7,000 and <= 10 mins old
- 👎 for all other cases
        """)
        
        # Determine rating based on exact rules
        if market_cap_value >= 50000:  # Super high market cap gets 🔥 regardless of age
            rating = '🔥'
            reason = f"Exceptionally high market cap (${market_cap_value:,.0f} >= $50,000)"
        elif market_cap_value >= 10000 and age_minutes <= 10:
            rating = '🔥'
            reason = f"High market cap (${market_cap_value:,.0f} >= $10,000) and new (<= 10 mins)"
        elif market_cap_value >= 7000:
            rating = '👍'
            reason = f"Good market cap (${market_cap_value:,.0f} >= $7,000)"
        elif market_cap_value < 5000 and age_minutes > 10:
            rating = '💀'
            reason = f"Low market cap (${market_cap_value:,.0f} < $5,000) and old (> 10 mins)"
        elif 5000 <= market_cap_value < 7000:
            if age_minutes <= 10:
                rating = '🆙'
                reason = f"Decent market cap (${market_cap_value:,.0f} between $5,000-$7,000) and new (<= 10 mins)"
            else:
                rating = '👎'
                reason = f"Decent market cap (${market_cap_value:,.0f} between $5,000-$7,000) but old (> 10 mins)"
        else:
            rating = '👎'
            reason = "Default case - doesn't meet any positive criteria"
        
        logger.info(f"""
=== Rating Calculation Complete ===
Token: {market_cap_str} ({time_str})
Market Cap: ${market_cap_value:,.0f}
Age: {age_minutes} minutes
Rating: {rating}
Reason: {reason}
        """)
        return rating
        
    except Exception as e:
        logger.error(f"Error calculating token rating for '{market_cap_str}': {e}")
        return '👎'

def get_market_status(avg_thumbs, high_potential_percentage, new_promising_count):
    """Determine the overall market status"""
    status = []
    
    # Check for hot market conditions
    if avg_thumbs > 1.5 and high_potential_percentage > 50:
        status.append("🔥 HOT - High activity with many promising agents")
    
    # Check for growing market
    if new_promising_count >= 2:
        status.append("🌱 GROWING - New promising agents appearing")
    
    # Check for active market
    if avg_thumbs > 0.5 or high_potential_percentage > 30:
        status.append("📈 ACTIVE - Good number of potential opportunities")
    
    # Check for cold market
    if avg_thumbs < 0.2 and high_potential_percentage < 20:
        status.append("🥶 COLD - Very limited activity")
    
    # Check for neutral market
    if not status:  # If no other conditions met
        status.append("😐 NEUTRAL - Average market activity")
    
    # Log market metrics
    logger.info(f"""
Market Status Metrics:
Average Thumbs: {avg_thumbs:.2f}
High Potential %: {high_potential_percentage:.1f}%
New Promising: {new_promising_count}
Status: {' | '.join(status)}
    """)
    
    return ' | '.join(status)

def analyze_market_summary(agents, num_recent=50):
    """Analyze the market status based on recent agents only"""
    try:
        logger.info(f"\n=== Starting Market Summary Analysis ===")
        
        # Sort by time for analysis
        time_sorted_agents = sorted(
            agents.values(),
            key=lambda x: x['time'],
            reverse=True
        )[:num_recent]
        
        # Also create a market cap sorted list
        market_cap_sorted_agents = sorted(
            agents.values(),
            key=lambda x: validate_market_cap(x['market_cap']),
            reverse=True  # Highest market cap first
        )[:10]  # Top 10 by market cap
        
        total_agents = len(time_sorted_agents)
        logger.info(f"Processing {total_agents} most recent agents")

        # Initialize counters
        analyses = []
        thumbs_up_count = 0
        high_potential_count = 0
        new_promising_count = 0
        promising_tokens = []
        agent_ratings = {}
        
        # First pass: Calculate ratings programmatically
        for agent in time_sorted_agents:
            logger.info(f"\nAnalyzing agent: {agent['name']}")
            logger.info(f"Market Cap: {agent['market_cap']}")
            logger.info(f"Time: {agent['time']}")
            
            # Get programmatic rating
            rating = get_token_rating(agent['market_cap'], agent['time'])
            agent_ratings[agent['name']] = rating
            logger.info(f"Assigned Rating: {rating}")
            
            # Count positive ratings
            if rating in ['🔥', '👍']:
                thumbs_up_count += 1
                high_potential_count += 1
                logger.info("Counted as high potential")
            elif rating == '🆙':
                thumbs_up_count += 1
                logger.info("Counted as positive")
            
            # Track new promising tokens
            if 'minutes ago' in agent['time']:
                market_cap_value = validate_market_cap(agent['market_cap'])
                if market_cap_value >= 10000:
                    new_promising_count += 1
                    logger.info("Counted as new promising")
            
            # Add promising tokens to detailed list
            if rating in ['🔥', '👍', '🆙']:
                token_url = f"https://fun.virtuals.io/agents/{agent.get('contract_address', '')}"
                promising_tokens.append({
                    'name': agent['name'],
                    'symbol': agent['symbol'],
                    'market_cap': agent['market_cap'],
                    'time': agent['time'],
                    'url': token_url,
                    'rating': rating,
                    'description': agent['description'][:100] + "..." if len(agent['description']) > 100 else agent['description']
                })
                logger.info("Added to promising tokens list")

        # Calculate market metrics
        avg_thumbs = thumbs_up_count / total_agents if total_agents > 0 else 0
        high_potential_percentage = (high_potential_count / total_agents * 100) if total_agents > 0 else 0
        
        # Count new promising coins (less than 10 mins old with good metrics)
        new_promising_count = sum(
            1 for agent in time_sorted_agents  # Using time_sorted_agents here
            if 'minutes ago' in agent['time'] 
            and validate_market_cap(agent['market_cap']) >= 7000
        )
        
        logger.info(f"""
=== Market Summary Statistics ===
Total Agents: {total_agents}
Positive Ratings: {thumbs_up_count} (Avg: {avg_thumbs:.2f})
High Potential: {high_potential_count} ({high_potential_percentage:.1f}%)
New Promising: {new_promising_count}
Total Promising Tokens: {len(promising_tokens)}
        """)

        # Generate summary text with market status
        market_status = get_market_status(avg_thumbs, high_potential_percentage, new_promising_count)
        
        summary = f"""
=== Market Summary (Last {num_recent} Agents) ===
Recent Agents Analyzed: {total_agents}
Total Positive Ratings: {thumbs_up_count} (Average: {avg_thumbs:.2f} per agent)
High Potential Agents: {high_potential_count} ({high_potential_percentage:.1f}%)
New Promising Coins: {new_promising_count}

Market Status: {market_status}

🌟 PROMISING TOKENS 🌟
"""
        # Add promising tokens details
        if promising_tokens:
            for token in promising_tokens:
                summary += f"""
{'-' * 50}
Name: {token['name']} ({token['symbol']})
Market Cap: {token['market_cap']}
Time Created: {token['time']}
Rating: {token['rating']}
URL: {token['url']}

Description:
{token['description']}
"""
        else:
            summary += "\nNo highly promising tokens found in recent analysis."

        # Add market activity section with top market caps
        summary += f"""
{'-' * 50}
📊 Top Market Cap Tokens (by USD value):
"""
        # Add top 10 by market cap with proper formatting
        for agent in market_cap_sorted_agents:
            market_cap_value = validate_market_cap(agent['market_cap'])
            age_minutes = get_token_age_minutes(agent['time'])
            
            # Get rating with proper emoji based on exact criteria
            if market_cap_value >= 50000:  # Super high market cap
                rating = '🔥'  # Hot token regardless of age
            elif market_cap_value >= 10000 and age_minutes <= 10:
                rating = '🔥'  # Hot new token
            elif market_cap_value >= 7000:
                if age_minutes <= 3:  # Very new
                    rating = '🆕 👍'  # New good token
                else:
                    rating = '👍'  # Good token
            elif market_cap_value < 5000 and age_minutes > 10:
                rating = '💀'  # Dead token
            elif 5000 <= market_cap_value < 7000:
                if age_minutes <= 10:
                    rating = '🆙'  # New token with decent cap
                else:
                    rating = '👎'  # Limited potential
            else:
                rating = '👎'  # Default case
            
            logger.info(f"Top token: {agent['name']} - Cap: ${market_cap_value:,.0f} - Age: {age_minutes}m - Rating: {rating}")
            
            # Format the display string with proper market cap and rating
            summary += (f"- {agent['name']} ({agent['symbol']}) - "
                       f"{agent['market_cap']} (${market_cap_value:,.0f}) - "
                       f"{rating} - {agent['time']}\n")

        logger.info("Market summary generated successfully")
        return summary
        
    except Exception as e:
        logger.error(f"Error generating market summary: {e}")
        return "Error generating market summary"

def monitor_changes(url):
    """Continuously monitor changes on the webpage."""
    global previous_agents
    logger.info(f"Starting monitoring of {url}")

    driver = setup_driver()
    try:
        # Initial fetch and display
        page_content = fetch_page_content(driver, url)
        if page_content:
            current_agents = parse_and_find_updates(page_content)
            if current_agents:
                # Display only recent agents
                display_all_agents(current_agents, num_recent=50)
                
                # Generate and display market summary for recent agents
                market_summary = analyze_market_summary(current_agents, num_recent=50)
                print("\nMarket Summary:")
                print(market_summary)
                
                # Log initial state and market summary
                with open("updates_log.txt", "a") as log_file:
                    log_file.write(f"\n=== Initial State at {datetime.now().isoformat()} ===\n")
                    # Sort and get recent agents for logging
                    sorted_agents = sorted(
                        current_agents.values(),
                        key=lambda x: x['time'],
                        reverse=True
                    )[:10]
                    
                    for data in sorted_agents:
                        log_file.write(f"\n- {data['name']} ({data['symbol']})\n")
                        log_file.write(f"  Market Cap: {data['market_cap']}\n")
                        log_file.write(f"  Creator: {data['creator']}\n")
                        log_file.write(f"  Time: {data['time']}\n")
                        log_file.write(f"  Description: {data['description']}\n")
                    log_file.write("\n" + market_summary + "\n")
                
                previous_agents = current_agents
            else:
                logger.error("Failed to parse initial agents data")
                return
        else:
            logger.error("Failed to fetch initial page content")
            return

        # Continue with monitoring loop
        while True:
            try:
                logger.info(f"Checking for updates on {url}")

                # Fetch the webpage content
                page_content = fetch_page_content(driver, url)
                if page_content is None:
                    logger.warning("Failed to fetch content, waiting before retry")
                    time.sleep(60)
                    continue

                # Parse current agents
                current_agents = parse_and_find_updates(page_content)
                
                # Detect and log changes
                changes = detect_agent_changes(current_agents, previous_agents)
                if any(changes.values()):
                    logger.info("Changes detected in agents data")
                    log_changes(changes)
                    
                    # Print new agents to console
                    if changes["new"]:
                        print("\n🆕 New Agents Detected:")
                        for agent in changes["new"]:
                            print("\n" + "-"*30)
                            print(f"Name: {agent['name']} ({agent['symbol']})")
                            print(f"Market Cap: {agent['market_cap']}")
                            print(f"Creator: {agent['creator']}")
                            print(f"Time: {agent['time']}")
                            print(f"Description: {agent['description']}")
                else:
                    logger.info("No changes detected in agents data")

                # Update previous state
                previous_agents = current_agents

                # Wait before next check
                time.sleep(60)

            except Exception as e:
                logger.error(f"Unexpected error in monitoring loop: {e}", exc_info=True)
                time.sleep(60)

    finally:
        driver.quit()

if __name__ == "__main__":
    try:
        monitor_changes(URL)
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
