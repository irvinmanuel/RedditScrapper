from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import urllib.parse
import time

def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def search_reddit(driver, query):
    full_query = urllib.parse.urlencode({"q": f"{query} site:reddit.com", "kl": "us-en"})
    url = f"https://www.duckduckgo.com/?{full_query}"

    driver.get(url)

    # Wait for results to load
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='result']"))
    )

    time.sleep(1)

    soup = BeautifulSoup(driver.page_source, "html.parser")

    links = []
    for result in soup.find_all("article", {"data-testid": "result"}):
        a = result.find("a", {"data-testid": "result-title-a"})
        snippet_tag = result.find("div", {"data-result": "snippet"})

        if a:
            href = a["href"]
            title = a.get_text(strip=True)
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            if "reddit.com" in href and href not in [l["url"] for l in links]:
                links.append({
                    "title": title,
                    "url": href,
                    "snippet": snippet
                })

        if len(links) >= 10:
            break

    return links


def main():
    print("🦆 Reddit Search via DuckDuckGo + Selenium")
    print("─" * 40)
    print("Starting browser...")

    driver = get_driver()

    try:
        while True:
            query = input("\nSearch: ").strip()

            if query.lower() == "exit":
                print("Bye!")
                break

            if not query:
                print("Please enter a search term.")
                continue

            print(f"\n🔎 Results for: '{query} site:reddit.com'\n")

            links = search_reddit(driver, query)

            if not links:
                print("No results found, try again.")
                continue
            with open("search_results.txt", "w", encoding="utf-8") as f:
                for i, item in enumerate(links, 1):
                    f.write(f"{i}. {item['title']}\n")
                    f.write(f"   🔗 {item['url']}\n")
                   
           
    finally:
        driver.quit()


main()