import json
import os
import re
import requests
import time
from collections import Counter
from multiprocessing import Process
from dotenv import load_dotenv 

# Third-party libraries
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.safari.options import Options as SafariOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, ElementClickInterceptedException

# --- CONFIGURATION AND SETUP ---

BASE_URL = "https://elpais.com"
IMAGES_DIR = "scraped_images"

# Load BrowserStack credentials and capabilities
try:
    with open('config.json', 'r') as f:
        CONFIG = json.load(f)
    BS_CONFIG = CONFIG['browserstack_config']
    PARALLEL_CAPS = CONFIG['parallel_capabilities']
except Exception as e:
    print(f"Error loading config.json: {e}")
    exit()

def rapidapi_translate(text, source_lang, target_lang):
    """
    Translates text using Rapid Translate Multi Traduction API via requests.
    Credentials are loaded from environment variables: RAPID_API_KEY and RAPID_API_HOST.
    """
    load_dotenv()
    api_key = os.getenv("RAPID_API_KEY")
    api_host = os.getenv("RAPID_API_HOST")
    if not api_key or not api_host:
        print("Warning: RAPID_API_KEY and RAPID_API_HOST not set. Skipping translation.")
        return "[Translation not configured]"

    url = f"https://{api_host}/t"
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": api_host,
        "Content-Type": "application/json"
    }
    
    # The API expects an array format
    payload = {
        "from": source_lang,
        "to": target_lang,
        "q": [text]  # Array format
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        result = response.json()
        
        # Debug: print response to understand format
        # print(f"Translation API Response: {result}")
        
        # Try multiple response formats
        if isinstance(result, list) and len(result) > 0:
            # If it's a direct list of translations
            if isinstance(result[0], str):
                return result[0]
            elif isinstance(result[0], dict):
                return result[0].get('translatedText', result[0].get('translated', text))
        elif isinstance(result, dict):
            # Check for nested structure
            if 'translatedText' in result:
                trans = result['translatedText']
                return trans[0] if isinstance(trans, list) else trans
            elif 'translated' in result:
                trans = result['translated']
                return trans[0] if isinstance(trans, list) else trans
            elif 'translations' in result:
                trans = result['translations']
                if isinstance(trans, list) and len(trans) > 0:
                    return trans[0].get('text', trans[0].get('translatedText', text))
        
        return text
    except requests.exceptions.HTTPError as e:
        print(f"Translation HTTP Error: {e.response.status_code} - {e.response.text[:100]}")
        return f"[Translation failed: HTTP {e.response.status_code}]"
    except Exception as e:
        print(f"Translation Error: {str(e)[:100]}")
        return f"[Translation failed: {str(e)[:50]}]"

# --- CORE LOGIC CLASS ---

class ElPaisScraperBrowserStack:
    def __init__(self, browserstack_caps: dict):
        self.caps = browserstack_caps
        self.driver = None
        self.scraped_articles = []
        self.session_name = self.caps.get("bstack:options", {}).get("sessionName", "Unknown Session")
        self.setup_driver()

    def setup_driver(self):
        """Sets up the remote WebDriver for BrowserStack, enforcing Spanish language."""
        
        bstack_options = self.caps.get("bstack:options", {})
        browser_name = self.caps.get("browserName", "").lower()

        # Determine options based on browser
        if 'chrome' in browser_name:
            options = ChromeOptions()
            options.add_argument('--lang=es')
            options.add_experimental_option('prefs', {'intl.accept_languages': 'es,es-ES'})
        elif 'firefox' in browser_name:
            options = FirefoxOptions()
            options.set_preference("intl.accept_languages", "es,es-ES")
        elif 'edge' in browser_name:
            options = EdgeOptions()
            options.add_argument('--lang=es')
            options.add_experimental_option('prefs', {'intl.accept_languages': 'es,es-ES'})
        elif 'safari' in browser_name:
            options = SafariOptions()
            # Safari doesn't support language preferences via options
        else:
            # Default to ChromeOptions
            options = ChromeOptions()
            options.add_argument('--lang=es')
            options.add_experimental_option('prefs', {'intl.accept_languages': 'es,es-ES'})
            
        # Set BrowserStack capabilities in options
        for key, value in bstack_options.items():
            options.set_capability(f"bstack:{key}", value)
        
        # Set other capabilities
        for key, value in self.caps.items():
            if key != "bstack:options":
                options.set_capability(key, value)
            
        try:
            print(f"Starting session for: {self.session_name}")
            # Build authenticated URL
            bs_url = f"https://{BS_CONFIG['user']}:{BS_CONFIG['key']}@hub-cloud.browserstack.com/wd/hub"
            self.driver = webdriver.Remote(
                command_executor=bs_url,
                options=options
            )
            self.driver.set_page_load_timeout(30)
        except WebDriverException as e:
            print(f"Error connecting to BrowserStack for {self.session_name}: {e}")
            raise

    def scrape_articles(self):
        """Navigates to Opinion, scrapes 5 links, then performs deep scraping and image download."""
        
        if not os.path.exists(IMAGES_DIR):
            os.makedirs(IMAGES_DIR)

        try:
            self.driver.get(BASE_URL)
            time.sleep(3)  # Allow page to fully load
            
            # Handle cookie consent or popups
            try:
                cookie_buttons = [
                    (By.ID, "didomi-notice-agree-button"),
                    (By.XPATH, "//button[contains(text(), 'Aceptar')]"),
                    (By.XPATH, "//button[contains(text(), 'Accept')]"),
                    (By.CSS_SELECTOR, "button[class*='accept']"),
                ]
                for by, selector in cookie_buttons:
                    try:
                        btn = WebDriverWait(self.driver, 2).until(
                            EC.element_to_be_clickable((by, selector))
                        )
                        btn.click()
                        print(f"{self.session_name}: Closed cookie/consent banner")
                        time.sleep(1)
                        break
                    except:
                        continue
            except:
                pass
            
            # Navigate directly to Opinion section (more reliable)
            print(f"{self.session_name}: Navigating to Opinion section...")
            self.driver.get(f"{BASE_URL}/opinion/")
            time.sleep(3)

            # Find the first five articles with multiple fallback strategies
            article_elements = []
            article_selectors = [
                "article",
                ".c_a",
                "[data-dtm-region]",
                ".articulo",
                ".story",
                "h2 a",
            ]
            
            for selector in article_selectors:
                try:
                    article_elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if len(article_elements) >= 5:
                        print(f"{self.session_name}: Found articles using selector: {selector}")
                        break
                except NoSuchElementException:
                    continue
            
            article_links = []
            seen_urls = set()
            for i, element in enumerate(article_elements):
                if len(article_links) >= 5:
                    break
                try:
                    # Try multiple methods to get article URL
                    url = None
                    if element.tag_name == 'a':
                        url = element.get_attribute('href')
                    else:
                        # Look for anchor tag within element
                        try:
                            link_elem = element.find_element(By.TAG_NAME, 'a')
                            url = link_elem.get_attribute('href')
                        except:
                            pass
                    
                    # Try multiple methods to get title
                    title = None
                    title_selectors = [
                        (By.TAG_NAME, 'h2'),
                        (By.TAG_NAME, 'h3'),
                        (By.CSS_SELECTOR, '.c_t'),
                        (By.CSS_SELECTOR, '[data-dtm-region] a'),
                        (By.TAG_NAME, 'a'),
                    ]
                    
                    for by, sel in title_selectors:
                        try:
                            title_elem = element.find_element(by, sel)
                            title = title_elem.text.strip()
                            if title:
                                break
                        except:
                            continue
                    
                    # Validate URL is an article (not image, section link, etc.)
                    if url and title and ('//' in url or url.startswith('/')) and len(title) > 10:
                        if not url.startswith('http'):
                            url = BASE_URL + url if url.startswith('/') else BASE_URL + '/' + url
                        # Avoid duplicates and ensure it's a valid article URL
                        if url not in seen_urls and '/opinion/' in url:
                            article_links.append({'url': url, 'title_es': title})
                            seen_urls.add(url)
                            print(f"{self.session_name}: Found article {len(article_links)}: {title[:60]}...")
                except Exception as e:
                    print(f"{self.session_name}: Error extracting article {i}: {e}")
                    continue
            
            if not article_links:
                print(f"{self.session_name}: Error: Could not find any article links.")
                return

            # Deep scrape each article (full content + image)
            for i, article in enumerate(article_links):
                print(f"\n{self.session_name}: Scraping article {i+1}/5: {article['title_es'][:50]}...")
                self.driver.get(article['url'])
                time.sleep(2)

                # 2. SCRAPE FULL TITLE AND CONTENT
                try:
                    # Try multiple selectors for title
                    final_title_es = None
                    title_selectors = [
                        (By.TAG_NAME, 'h1'),
                        (By.CSS_SELECTOR, 'header h1'),
                        (By.CSS_SELECTOR, '.a_t'),
                        (By.CSS_SELECTOR, '.article-header h1'),
                        (By.CSS_SELECTOR, '[data-dtm-region="articulo_titulo"]'),
                    ]
                    
                    for by, sel in title_selectors:
                        try:
                            final_title_es = self.driver.find_element(by, sel).text
                            if final_title_es:
                                break
                        except:
                            continue
                    
                    if final_title_es:
                        article['title_es'] = final_title_es
                    
                    # Try multiple selectors for content
                    full_content_es = None
                    content_selectors = [
                        (By.CSS_SELECTOR, 'article[data-dtm-region]'),
                        (By.CSS_SELECTOR, '.article_body'),
                        (By.CSS_SELECTOR, '.a_c'),
                        (By.TAG_NAME, 'article'),
                        (By.CSS_SELECTOR, '.articulo-cuerpo'),
                    ]
                    
                    for by, sel in content_selectors:
                        try:
                            full_content_element = self.driver.find_element(by, sel)
                            full_content_es = full_content_element.text
                            if full_content_es and len(full_content_es) > 50:
                                break
                        except:
                            continue
                    
                    article['content_es'] = full_content_es if full_content_es else "CONTENT NOT SCRAPED"
                    
                except Exception as e:
                    print(f"{self.session_name}: Error scraping content: {e}")
                    article['content_es'] = "CONTENT NOT SCRAPED"
                    
                # 3. DOWNLOAD COVER IMAGE
                try:
                    img_url = None
                    img_selectors = [
                        (By.CSS_SELECTOR, 'article img'),
                        (By.CSS_SELECTOR, 'figure img'),
                        (By.CSS_SELECTOR, '.a_m img'),
                        (By.CSS_SELECTOR, '[data-dtm-region] img'),
                        (By.TAG_NAME, 'img'),
                    ]
                    
                    for by, sel in img_selectors:
                        try:
                            img_element = self.driver.find_element(by, sel)
                            img_url = img_element.get_attribute('src') or img_element.get_attribute('data-src')
                            if img_url and ('http' in img_url or img_url.startswith('/')):
                                break
                        except:
                            continue
                    
                    if img_url:
                        if not img_url.startswith('http'):
                            img_url = BASE_URL + img_url if img_url.startswith('/') else BASE_URL + '/' + img_url
                        article['image_path'] = self._download_image(img_url, i+1, self.session_name)
                    else:
                        article['image_path'] = "No image URL found"
                        
                except Exception as e:
                    print(f"{self.session_name}: Error downloading image: {e}")
                    article['image_path'] = "No cover image element found"
                    
                self.scraped_articles.append(article)

        except Exception as e:
            print(f"{self.session_name}: Critical error during scraping: {e}")
        finally:
            if self.driver:
                self.driver.quit()
                print(f"Session closed for: {self.session_name}")

    def _download_image(self, url, index, session_name):
        """Downloads an image from a URL and saves it locally."""
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            file_extension = url.split('.')[-1].split('?')[0]
            if not file_extension or len(file_extension) > 4:
                file_extension = 'jpg'
                
            safe_session_name = re.sub(r'[^a-zA-Z0-9]', '_', session_name)
            file_name = os.path.join(IMAGES_DIR, f"article_{index}_{safe_session_name}.{file_extension}")
            
            with open(file_name, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            
            return file_name
        except Exception:
            return f"Download failed."

# --- POST-PROCESSING AND ANALYSIS ---

def analyze_data(articles: list, session_name: str):
    """Prints Spanish titles/content, translates headers using the RapidAPI Multi Traduction API, and analyzes word frequency."""
    
    if not articles:
        print(f"\n--- {session_name}: ANALYSIS FAILED: No articles scraped. ---")
        return

    # Load environment variables (for RapidAPI)
    load_dotenv()

    all_translated_titles = []
    
    print("\n" + "="*50)
    print(f"--- {session_name}: RESULTS & ANALYSIS ---")
    print("="*50)
    
    for i, article in enumerate(articles):
        print(f"\n[ ARTICLE {i+1} ]")
        
        # Print title and content in Spanish
        print(f"Spanish Title: {article.get('title_es', 'N/A')}")
        print(f"Spanish Content (Snippet): {article.get('content_es', 'N/A')[:250]}...")
        print(f"Image Status: {article.get('image_path', 'N/A')}")
        
        # Translate headers to English using RapidAPI
        title_es = article.get('title_es', '')
        if title_es and article.get('content_es', '') != "CONTENT NOT SCRAPED":
            try:
                title_en = rapidapi_translate(title_es, "es", "en")
                all_translated_titles.append(title_en)
                print(f"Translated Header (EN): {title_en}")
            except Exception as e:
                print(f"Translation Error (RapidAPI): {e}")
        
        print("-" * 50)


    # Analyze Translated Headers for Repetition
    print(f"\n--- {session_name}: Word Repetition Analysis (Words repeated > 2 times) ---")
    
    combined_text = ' '.join(all_translated_titles)
    words = re.findall(r'\b\w+\b', combined_text.lower())
    stop_words = set(['the', 'a', 'an', 'and', 'to', 'in', 'is', 'it', 'for', 'of', 'on', 'with', 'from', 'at', 'by'])
    filtered_words = [word for word in words if word not in stop_words and len(word) > 1]
    word_counts = Counter(filtered_words)
    repeated_words = {word: count for word, count in word_counts.items() if count > 2}

    if repeated_words:
        print("\nWords Repeated More Than Twice:")
        for word, count in repeated_words.items():
            print(f" - '{word}': {count} times")
    else:
        print("No words were repeated more than twice across the translated headers (after filtering stop words).")


def run_test_process(caps: dict):
    """Executes the full scraper and analysis pipeline for a single thread."""
    session_name = caps.get('bstack:options', {}).get('sessionName', 'Unknown')
    try:
        scraper = ElPaisScraperBrowserStack(caps)
        scraper.scrape_articles()
        analyze_data(scraper.scraped_articles, session_name)
        print(f"\n{session_name}: Total articles scraped: {len(scraper.scraped_articles)}")
    except Exception as e:
        print(f"Test run failed for {session_name}: {e}")
        
# --- MAIN EXECUTION ---

if __name__ == '__main__':
    
    print("=" * 60)
    print("--- EL PAÍS SCRAPER (BROWSERSTACK PARALLEL TESTING) ---")
    print("=" * 60)
    
    processes = []
    
    # Initiate 5 parallel BrowserStack sessions
    print(f"\nStarting {len(PARALLEL_CAPS)} parallel test processes on BrowserStack...\n")
    
    for caps in PARALLEL_CAPS:
        p = Process(target=run_test_process, args=(caps,))
        processes.append(p)
        p.start()

    # Wait for all processes to complete
    for p in processes:
        p.join()

    print("\n" + "="*60)
    print("--- ALL BROWSERSTACK SESSIONS COMPLETE ---")
    print("="*60)
