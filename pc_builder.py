import asyncio
import json
import requests
import urllib.parse
import time
import uuid
import re
from urllib.parse import quote_plus

from crewai import Agent, Task, Crew, Process
from crewai.tools import tool
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
LLM_MODEL = "ollama/gemma4"
REDDIT_HEADERS = {"User-Agent": "Mozilla/5.0 (PCBuilder Bot/1.0)"}

# Distribució % pressupostos per categoria
BUDGET_DISTRIBUTION = {
    "CPU":           0.18,  # 18%
    "GPU":           0.35,  # 35%
    "RAM":           0.10,  # 10%
    "Placa Base":    0.08,  # 8%
    "Almacenamiento":0.07,  # 7%
    "Fuente":        0.09,  # 9%
    "Caja":          0.08,  # 8%
    "Refrigeración": 0.05,  # 5%
}

# Palabras clave que indican un PC completo o portátil — EXCLUIR
BLACKLIST_KEYWORDS = [
    "ordenador completo", "pc completo", "torre completa", "equipo completo",
    "sobremesa completo", "mini pc", "nuc", "barebones", "barebone",
    "portátil", "laptop", "notebook", "gaming pc completo",
    "all in one", "aio pc", "desktop pc completo",
    "computer system", "complete build", "prebuilt",
    "gaming desktop", "gaming computer", "pc gaming completo",
    "intel nuc", "beelink", "minisforum", "acemagic",
]

# Palabras que han de estar en els articles
CATEGORY_REQUIRED_KEYWORDS = {
    "CPU": ["processor", "procesador", "ryzen", "intel core", "i3", "i5", "i7", "i9",
            "r3", "r5", "r7", "r9", "threadripper", "athlon"],
    "GPU": ["graphics", "gráfica", "tarjeta gráfica", "rtx", "gtx", "rx ", "radeon",
            "geforce", "gpu", "video card", "vga", "arc a"],
    "RAM": ["ddr4", "ddr5", "memory", "memoria ram", "ram", "dimm", "so-dimm",
            "vengeance", "trident", "ripjaws", "fury"],
    "Placa Base": ["motherboard", "placa base", "placa madre", "mainboard", "atx",
                   "matx", "itx", "b650", "b550", "x670", "z790", "z690", "b760", "h770"],
    "Almacenamiento": ["ssd", "nvme", "m.2", "hard drive", "hdd", "disco duro",
                       "solid state", "pcie", "sata ssd"],
    "Fuente": ["power supply", "fuente de alimentación", "psu", "watt", "850w",
               "750w", "650w", "550w", "modular", "semi-modular"],
    "Caja": ["case", "caja", "torre", "chasis", "chassis", "mid tower", "mini tower",
             "full tower", "atx case", "matx case", "itx case"],
    "Refrigeración": ["cooler", "refrigeración", "disipador", "aio", "liquid cooler",
                      "cpu cooler", "fan", "heatsink", "ventilador cpu", "noctua", "be quiet"],
}


# ─────────────────────────────────────────────────────────────
# mirar que el no dongui un pc complet en alguna de les parts
# ─────────────────────────────────────────────────────────────
def is_complete_pc(title: str) -> bool:
    """Devuelve True si el título parece un PC completo o portátil."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in BLACKLIST_KEYWORDS)


def is_correct_component(title: str, categoria: str) -> bool:
    """Devuelve True si el título corresponde a la categoría buscada."""
    title_lower = title.lower()
    required = CATEGORY_REQUIRED_KEYWORDS.get(categoria, [])
    if not required:
        return True
    return any(kw in title_lower for kw in required)


def filter_products(products: list, categoria: str) -> list:
    """Filtra productos: elimina PCs completos y productos de categoría incorrecta."""
    filtered = []
    for p in products:
        title = p.get("title", "")
        if is_complete_pc(title):
            continue
        if not is_correct_component(title, categoria):
            continue
        filtered.append(p)
    return filtered


def parse_price(price_str: str) -> float:
    """Extrae el valor numérico de un string de precio."""
    try:
        cleaned = re.sub(r"[^\d.,]", "", price_str).replace(",", ".")
        # Si hay varios puntos, quedarse con el último segmento relevante
        parts = cleaned.split(".")
        if len(parts) > 2:
            cleaned = parts[0] + "." + parts[-1]
        return float(cleaned)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────
# SELENIUM 
# ─────────────────────────────────────────────────────────────
def get_selenium_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


# ─────────────────────────────────────────────────────────────
# REDDIT: BUSCAR URLs via DuckDuckGo + Selenium
# ─────────────────────────────────────────────────────────────
def search_reddit_urls(query: str, max_results: int = 5) -> list:
    print(f"\n🦆 [DuckDuckGo] Buscando: '{query}'")
    driver = get_selenium_driver()
    links = []
    try:
        full_query = urllib.parse.urlencode({"q": f"{query} site:reddit.com", "kl": "us-en"})
        driver.get(f"https://www.duckduckgo.com/?{full_query}")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='result']"))
        )
        time.sleep(1)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        for result in soup.find_all("article", {"data-testid": "result"}):
            a = result.find("a", {"data-testid": "result-title-a"})
            snippet_tag = result.find("div", {"data-result": "snippet"})
            if a:
                href = a["href"]
                title = a.get_text(strip=True)
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                if "reddit.com" in href and href not in [l["url"] for l in links]:
                    links.append({"title": title, "url": href, "snippet": snippet})
            if len(links) >= max_results:
                break
    except Exception as e:
        print(f"    Selenium error: {e}. Fallback a Reddit API...")
        links = _search_reddit_api_fallback(query, max_results)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"   {len(links)} posts encontrados")
    for i, l in enumerate(links, 1):
        print(f"     {i}. {l['title'][:75]}")
        print(f"         {l['url']}")
    return links


def _search_reddit_api_fallback(query: str, max_results: int = 5) -> list:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.reddit.com/search.json?q={encoded}&sort=relevance&limit={max_results}&type=link"
    try:
        r = requests.get(url, headers=REDDIT_HEADERS, timeout=15)
        data = r.json()
        links = []
        for post in data.get("data", {}).get("children", []):
            d = post.get("data", {})
            permalink = d.get("permalink", "")
            if permalink:
                links.append({
                    "title": d.get("title", ""),
                    "url": f"https://www.reddit.com{permalink}",
                    "snippet": d.get("selftext", "")[:200],
                })
        return links[:max_results]
    except Exception as e:
        print(f"    Reddit API fallback error: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# REDDIT: SCRAPING de un post via .json nativo
# ─────────────────────────────────────────────────────────────
def scrape_reddit_post(reddit_url: str) -> dict:
    clean = reddit_url.split("?")[0].rstrip("/")
    json_url = clean + ".json"
    print(f"\n [Reddit .json] {json_url}")
    try:
        r = requests.get(json_url, headers=REDDIT_HEADERS, timeout=15)
        data = r.json()
        post_data = data[0]["data"]["children"][0]["data"]
        title = post_data.get("title", "")
        selftext = post_data.get("selftext", "")

        comments = data[1]["data"]["children"]
        sorted_comments = sorted(
            [c for c in comments if c["kind"] == "t1"],
            key=lambda x: x["data"].get("score", 0),
            reverse=True,
        )

        top_comments = []
        for c in sorted_comments[:8]:
            body = c["data"].get("body", "").strip()
            score = c["data"].get("score", 0)
            if body and body not in ("[deleted]", "[removed]"):
                top_comments.append({"score": score, "body": body[:500]})

        result = {"url": reddit_url, "title": title, "selftext": selftext[:300], "top_comments": top_comments}
        print(f"   '{title[:70]}'")
        print(f"     {len(top_comments)} comentarios útiles")
        for c in top_comments[:3]:
            print(f"     [{c['score']} pts] {c['body'][:90]}...")
        return result
    except Exception as e:
        print(f"    Error: {e}")
        return {"url": reddit_url, "title": "Error", "selftext": "", "top_comments": []}


# ─────────────────────────────────────────────────────────────
# TIENDAS: Playwright scrapers
# ─────────────────────────────────────────────────────────────


async def _scrape_pccomponentes(page, query: str) -> list:
    search_url = f"https://www.pccomponentes.com/search?query={quote_plus(query)}&sort=sales"
    api_url = f"https://www.pccomponentes.com/api/articles/search-list?url={quote_plus(search_url)}"
    await page.goto(
        f"https://www.pccomponentes.com/search/?query={quote_plus(query)}",
        wait_until="domcontentloaded", timeout=60000,
    )
    await page.wait_for_timeout(2000)
    response = await page.evaluate(
        """async ({ url }) => {
            const res = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': '*/*',
                    'x-channel': 'e24bd484-e84d-4051-8c51-551bf17a0610',
                    'x-host': 'www.pccomponentes.com',
                    'x-selected-language': 'es-ES',
                }
            });
            return await res.text();
        }""",
        {"url": api_url},
    )
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        return []
    articles = data.get("dynamicData", {}).get("articles", [])
    products = []
    for art in articles[:8]:
        name = art.get("name", "N/A")
        price = art.get("promotionalPrice") or art.get("price")
        final_price = f"{price} €" if price else None
        slug = art.get("slug", "")
        url = f"https://www.pccomponentes.com/{slug}" if slug else None
        if name and final_price and url:
            products.append({"source": "PcComponentes", "title": name, "price": final_price, "rating": str(art.get("ratingAvg", "N/A")), "url": url})
    return products


async def _scrape_wallapop(page, query: str) -> list:
    search_id = str(uuid.uuid4())
    device_id = str(uuid.uuid4())
    api_url = (
        f"https://api.wallapop.com/api/v3/search/section"
        f"?keywords={quote_plus(query)}&source=deep_link"
        f"&search_id={search_id}&latitude=41.3891&longitude=2.1606"
        f"&category_id=24200&order_by=most_relevance&section_type=organic_search_results"
    )
    await page.goto("https://es.wallapop.com/", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    response = await page.evaluate(
        """async ({ url, deviceId }) => {
            const res = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'x-appversion': '820160',
                    'x-deviceos': '0',
                    'x-deviceid': deviceId,
                    'Origin': 'https://es.wallapop.com',
                    'Referer': 'https://es.wallapop.com/',
                }
            });
            return await res.text();
        }""",
        {"url": api_url, "deviceId": device_id},
    )
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        return []
    items = data.get("data", {}).get("section", {}).get("items", [])
    products = []
    for item in items[:8]:
        title = item.get("title", "N/A")
        price_obj = item.get("price", {})
        amount = price_obj.get("amount")
        price = f"{amount} EUR" if amount is not None else None
        web_slug = item.get("web_slug", "")
        url = f"https://es.wallapop.com/item/{web_slug}" if web_slug else None
        if title and price and url:
            products.append({"source": "Wallapop", "title": title, "price": price, "rating": "N/A", "url": url})
    return products


async def _scrape_all_async(query: str, categoria: str) -> list:
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="es-ES",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        pcc = await _scrape_pccomponentes(page, query)
        wallapop = await _scrape_wallapop(page, query)
        await browser.close()
    return pcc + wallapop


def scrape_stores(query: str, categoria: str) -> list:
    return asyncio.run(_scrape_all_async(query, categoria))


# ─────────────────────────────────────────────────────────────
# PASO 1: Reddit scraping
# ─────────────────────────────────────────────────────────────
def step_reddit(descripcion: str, presupuesto: str) -> str:
    print("\n" + "═"*55)
    print("  PASO 1 — Investigación Reddit")
    print("═"*55)

    queries = [
        f"build PC {presupuesto} euros 2025",
        f"gaming PC {presupuesto} euros gaming",
        descripcion
    ]

    all_urls = []
    for q in queries:
        urls = search_reddit_urls(q, max_results=3)
        for u in urls:
            if u["url"] not in [x["url"] for x in all_urls]:
                all_urls.append(u)
        if len(all_urls) >= 5:
            break

    scraped = []
    for item in all_urls[:5]:
        post = scrape_reddit_post(item["url"])
        if post["top_comments"]:
            scraped.append(post)
        if len(scraped) >= 3:
            break

    context = f"Descripción: {descripcion}\nPresupuesto: {presupuesto}\n\n"
    context += "=== POSTS DE REDDIT ===\n\n"
    for p in scraped:
        context += f"POST: {p['title']}\nURL: {p['url']}\n"
        if p['selftext']:
            context += f"Descripción: {p['selftext']}\n"
        context += "Comentarios:\n"
        for c in p['top_comments']:
            context += f"  [{c['score']} pts] {c['body']}\n"
        context += "\n---\n\n"

    return context


# ─────────────────────────────────────────────────────────────
# PASO 2: LLM planifica componentes con distribución de presupuesto
# ─────────────────────────────────────────────────────────────
def step_plan_components(reddit_context: str, descripcion: str, presupuesto: str) -> list:
    print("\n" + "═"*55)
    print("  PASO 2 — Planificación de componentes (LLM)")
    print("═"*55)

    # Calcular presupuesto numérico
    presupuesto_num = parse_price(presupuesto)
    if presupuesto_num == 0:
        presupuesto_num = 1200.0

    # Calcular presupuesto por categoría
    budget_breakdown = {}
    breakdown_text = "DISTRIBUCIÓN DE PRESUPUESTO OBLIGATORIA:\n"
    for cat, pct in BUDGET_DISTRIBUTION.items():
        amount = presupuesto_num * pct
        budget_breakdown[cat] = amount
        breakdown_text += f"  • {cat}: {pct*100:.0f}% = {amount:.0f}€ máximo\n"

    print(f"\n   Presupuesto total: {presupuesto_num:.0f}€")
    print(f"  Distribución:")
    for cat, amount in budget_breakdown.items():
        pct = BUDGET_DISTRIBUTION[cat]
        print(f"     • {cat:15} {pct*100:.0f}%  →  {amount:.0f}€")

    agente = Agent(
        role="Planificador de PC",
        goal="Generar lista JSON de componentes individuales basándote en Reddit y el presupuesto asignado.",
        backstory=(
            "Eres un experto en hardware PC. Propones SOLO componentes individuales "
            "(nunca PCs completos, nunca laptops, nunca barebones). "
            "Cada componente debe ser compatible con los demás y respetar el presupuesto asignado."
        ),
        llm=LLM_MODEL,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )

    tarea = Task(
        description=(
            f"{reddit_context}\n\n"
            f"Usuario: '{descripcion}'\nPresupuesto total: {presupuesto_num:.0f}€\n\n"
            f"{breakdown_text}\n"
            "REGLAS ESTRICTAS:\n"
            "1. SOLO componentes individuales (CPU sola, GPU sola, etc.) — NUNCA un PC completo\n"
            "2. Respetar el presupuesto máximo de cada categoría\n"
            "3. Todos los componentes deben ser compatibles entre sí\n"
            "4. Usar modelos reales que existen en el mercado europeo en 2026\n"
            "5. CPU y Placa Base deben compartir socket (ej: AM5 para Ryzen 7000, LGA1700 para Intel 12/13/14gen)\n\n"
            "RESPONDE ÚNICAMENTE con JSON válido, sin texto extra, sin markdown, sin ```:\n" \
            "con el siguiente formato:\n" \
                
            '[{"categoria":"CPU","modelo":"AMD Ryzen 5 7600","query_busqueda":"Ryzen 5 7600 procesador","precio_objetivo":"190€"},'
            '{"categoria":"GPU","modelo":"RTX 4060 Ti","query_busqueda":"RTX 4060 Ti tarjeta grafica","precio_objetivo":"380€"},'
            '{"categoria":"RAM","modelo":"...","query_busqueda":"...","precio_objetivo":"..."},'
            '{"categoria":"Placa Base","modelo":"...","query_busqueda":"...","precio_objetivo":"..."},'
            '{"categoria":"Almacenamiento","modelo":"...","query_busqueda":"...","precio_objetivo":"..."},'
            '{"categoria":"Fuente","modelo":"...","query_busqueda":"...","precio_objetivo":"..."},'
            '{"categoria":"Caja","modelo":"...","query_busqueda":"...","precio_objetivo":"..."}]'
        ),
        expected_output="JSON puro con lista de 7 componentes individuales. con el siguiente formato:\n" \
            '[{"categoria":"CPU","modelo":"AMD Ryzen 5 7600","query_busqueda":"Ryzen 5 7600 procesador","precio_objetivo":"190€"}, ... ]',
        agent=agente,
    )

    crew = Crew(agents=[agente], tasks=[tarea], process=Process.sequential, verbose=False)
    result = str(crew.kickoff()).strip()

    try:
        start = result.find("[")
        end = result.rfind("]") + 1
        if start != -1 and end > start:
            components = json.loads(result[start:end])
            # Validar que tenemos los campos necesarios
            valid = [c for c in components if c.get("categoria") and c.get("modelo") and c.get("query_busqueda")]
            if valid:
                print(f"\n   {len(valid)} componentes planificados:")
                for c in valid:
                    cat = c.get('categoria', '?')
                    budget_max = budget_breakdown.get(cat, 0)
                    print(f"     • {cat:15} → {c.get('modelo','?')[:45]}")
                    print(f"       Objetivo: {c.get('precio_objetivo','?')}  (máx. {budget_max:.0f}€)")
                return valid
    except json.JSONDecodeError as e:
        print(f"    Error JSON: {e}\n  Raw: {result[:300]}")

    # Fallback con distribución correcta
    print("    Usando componentes por defecto (fallback)...")
    return [
        {"categoria": "CPU",            "modelo": "AMD Ryzen 5 7600",           "query_busqueda": "Ryzen 5 7600 procesador AM5",         "precio_objetivo": f"{budget_breakdown['CPU']:.0f}€"},
        {"categoria": "GPU",            "modelo": "RTX 4060 Ti 8GB",            "query_busqueda": "RTX 4060 Ti tarjeta grafica",          "precio_objetivo": f"{budget_breakdown['GPU']:.0f}€"},
        {"categoria": "RAM",            "modelo": "Corsair Vengeance 32GB DDR5", "query_busqueda": "DDR5 32GB 6000 memoria ram",           "precio_objetivo": f"{budget_breakdown['RAM']:.0f}€"},
        {"categoria": "Placa Base",     "modelo": "Solo busca placas bases que funcionen con el soquet",       "query_busqueda": "MSI B650M Pro WiFi AM5 placa base",    "precio_objetivo": f"{budget_breakdown['Placa Base']:.0f}€"},
        {"categoria": "Almacenamiento", "modelo": "Samsung 990 Evo 1TB NVMe",   "query_busqueda": "Samsung 990 Evo 1TB NVMe SSD M.2",     "precio_objetivo": f"{budget_breakdown['Almacenamiento']:.0f}€"},
        {"categoria": "Fuente",         "modelo": "Be Quiet Pure Power 12M 650W","query_busqueda": "Be Quiet Pure Power 650W fuente",     "precio_objetivo": f"{budget_breakdown['Fuente']:.0f}€"},
        {"categoria": "Caja",           "modelo": "Fractal Design Pop Mini Air", "query_busqueda": "Fractal Pop Mini caja PC",             "precio_objetivo": f"{budget_breakdown['Caja']:.0f}€"},
    ]


# ─────────────────────────────────────────────────────────────
# PASO 3: Scraping tiendas + selección con datos reales
# ─────────────────────────────────────────────────────────────
def step_select_products(components: list, presupuesto_total: float) -> list:
    print("\n" + "═"*55)
    print("  PASO 3 — Búsqueda en tiendas y selección")
    print("═"*55)

   

    selected = []

    for comp in components:
        categoria = comp.get("categoria", "?")
        query = comp.get("query_busqueda", comp.get("modelo", ""))
        precio_obj_str = comp.get("precio_objetivo", "0€")
        precio_obj = parse_price(precio_obj_str)
        budget_max = presupuesto_total * BUDGET_DISTRIBUTION.get(categoria, 0.10)

        print(f"\n  🔍 [{categoria}] '{query}' (máx. {budget_max:.0f}€)")

        # Scraping real
        all_products = scrape_stores(query, categoria)

        # Filtrar PCs completos y categoría incorrecta
        products = filter_products(all_products, categoria)
        filtered_out = len(all_products) - len(products)
        if filtered_out > 0:
            print(f"      {filtered_out} productos descartados (PCs completos o categoría incorrecta)")

        if not products:
            print(f"       Sin resultados válidos para '{query}'")
            # Si no hay resultados con filtro estricto, intentar sin filtro de categoría
            products = [p for p in all_products if not is_complete_pc(p.get("title", ""))]
            if not products:
                selected.append({
                    "categoria": categoria,
                    "nombre": comp.get("modelo", "N/A"),
                    "precio": precio_obj_str,
                    "tienda": "N/A — sin stock encontrado",
                    "url": "N/A",
                })
                continue

        # Filtrar por precio razonable (no más del doble del presupuesto máximo)
        products_in_budget = [
            p for p in products
            if 0 < parse_price(p["price"]) <= budget_max * 2
        ]
        if products_in_budget:
            products = products_in_budget

        print(f"      {len(products)} productos válidos encontrados:")
        for i, p in enumerate(products[:5], 1):
            print(f"        {i}. [{p['source']}] {p['title'][:55]}")
            print(f"            {p['price']}   {p['rating']}")
            print(f"            {p['url']}")


        agente_selector = Agent(
            role="Selector de componentes PC",
            goal="Elegir el mejor componente individual de una lista de productos reales scrapeados.",
            backstory=(
                "Eres un experto en compras de hardware. Recibes una lista de productos reales "
                "de tiendas online y eliges el componente más adecuado. NUNCA eliges PCs completos, "
                "portátiles, barebones o equipos pre-montados si no te lo piden. Solo componentes individuales."
                "Cuando busques los distintos componentes, no seas tan especifico con las busquedas, por ejemplo," \
                "'Kingston HyperX Fury RGB 16GB DDR5-5600MHz RAM', busca 'DDR5 16GB RAM' o 'DDR5 16GB memoria ram' para obtener más resultados y luego elige el mejor entre ellos." \
                "Con la placa base solo busca placas bases que sean compatibles con el socket de la CPU propuesta, por ejemplo, si la CPU es AM5, busca 'placa base AM5' o 'motherboard AM5' para obtener resultados compatibles y luego elige el mejor entre ellos." \
            ),
            llm=LLM_MODEL,
            tools=[],
            verbose=False,
            allow_delegation=False,
        )
        # El LLM elige entre productos REALES con contexto de presupuesto
        products_text = json.dumps(products[:8], ensure_ascii=False, indent=2)
        tarea_sel = Task(
            description=(
                f"Necesito el mejor componente de tipo: {categoria}\n"
                f"Modelo objetivo: {comp.get('modelo', '')}\n"
                f"Presupuesto máximo para esta pieza: {budget_max:.0f}€\n\n"
                f"PRODUCTOS REALES disponibles (URLs y precios son exactos y reales):\n{products_text}\n\n"
                "INSTRUCCIONES:\n"
                f"1. Elige SOLO un componente de tipo '{categoria}' — nunca un PC completo, portátil o barebone\n"
                "2. Prioriza el que más se acerque al presupuesto máximo sin superarlo (si es posible)\n"
                "3. Si todos superan el presupuesto, elige el más barato disponible\n"
                "4. La URL debe ser EXACTAMENTE una de las URLs de la lista anterior\n\n"
                "RESPONDE SOLO con este JSON (sin texto extra, sin markdown, sin ```):\n"
                '{"nombre":"título exacto del producto de la lista","precio":"precio exacto de la lista","tienda":"source exacto de la lista","url":"url exacta de la lista"}'
            ),
            expected_output='JSON objeto: {"nombre":"...","precio":"...","tienda":"...","url":"..."}',
            agent=agente_selector,
        )

        crew_sel = Crew(agents=[agente_selector], tasks=[tarea_sel], process=Process.sequential, verbose=False)
        result = str(crew_sel.kickoff()).strip()

        
        chosen = None
        try:
            start = result.find("{")
            end = result.rfind("}") + 1
            if start != -1 and end > start:
                candidate = json.loads(result[start:end])
                # Verificar que la URL be de productes reals
                real_urls = [p["url"] for p in products]
                if candidate.get("url") in real_urls:
                    chosen = candidate
                else:
                    print(f"       LLM inventó URL — seleccionando el mejor automáticamente")
        except (json.JSONDecodeError, Exception):
            print(f"       Error parseando respuesta LLM — seleccionando automáticamente")

        # seleccio automatica si el llm falla
        if not chosen:
            in_budget = sorted(
                [p for p in products if parse_price(p["price"]) <= budget_max],
                key=lambda x: parse_price(x["price"]),
                reverse=True,  # El más caro dentro del presupuesto = mejor relación calidad
            )
            fallback = in_budget[0] if in_budget else products[0]
            chosen = {
                "nombre": fallback["title"],
                "precio": fallback["price"],
                "tienda": fallback["source"],
                "url": fallback["url"],
            }

        chosen["categoria"] = categoria
        price_val = parse_price(chosen["precio"])
        over_budget = "  SOBRE PRESUPUESTO" if price_val > budget_max else ""
        print(f"\n      ELEGIDO: {chosen['nombre'][:60]}")
        print(f"         {chosen['precio']} (máx. {budget_max:.0f}€){over_budget}")
        print(f"         {chosen['tienda']}")
        print(f"         {chosen['url']}")
        selected.append(chosen)

    return selected

# ─────────────────────────────────────────────────────────────
# PASO 4: Agente Revisor — valida el output final
# ─────────────────────────────────────────────────────────────
def step_review(selected: list, presupuesto_total: float, descripcion: str) -> None:
    print("\n" + "═"*55)
    print("  PASO 4 — Revisión final (Agente Revisor)")
    print("═"*55)

   

    # Calcular pressupost € per categoria
    budget_info = ""
    total_real = 0.0
    components_text = ""
    for item in selected:
        cat = item.get("categoria", "?")
        precio_val = parse_price(item.get("precio", "0"))
        budget_max = presupuesto_total * BUDGET_DISTRIBUTION.get(cat, 0.10)
        total_real += precio_val
        components_text += (
            f"- {cat}: {item.get('nombre','N/A')} | "
            f"Precio: {item.get('precio','N/A')} | "
            f"Tienda: {item.get('tienda','N/A')} | "
            f"Presupuesto máx. categoría: {budget_max:.0f}€\n"
        )

    agente_revisor = Agent(
        role="Revisor de configuraciones PC",
        goal="Verificar que la lista de componentes seleccionados es coherente, compatible y ajustada al presupuesto.",
        backstory=(
            "Eres un ingeniero de hardware con 15 años de experiencia. "
            "Revisas listas de componentes y detectas incompatibilidades, "
            "componentes incorrectos (PCs completos, portátiles), desequilibrios de presupuesto "
            "o combinaciones que no tienen sentido técnico."
        ),
        llm=LLM_MODEL,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )
    tarea_revision = Task(
        description=(
            f"El usuario quería: '{descripcion}'\n"
            f"Presupuesto total: {presupuesto_total:.0f}€\n"
            f"Total gastado: {total_real:.0f}€\n\n"
            f"COMPONENTES SELECCIONADOS:\n{components_text}\n\n"
            "Revisa esta configuración y responde en español con este formato EXACTO:\n\n"
            "VEREDICTO: :white_check_mark: CORRECTO  o  VEREDICTO: :x: INCORRECTO\n\n"
            "ANÁLISIS:\n"
            "- Compatibilidad: [¿CPU y placa base tienen el mismo socket? ¿RAM compatible?]\n"
            "- Presupuesto: [¿Se respeta el presupuesto total? ¿Alguna categoría desproporcionada?]\n"
            "- Componentes: [¿Son todos piezas individuales? ¿Algún PC completo o portátil colado?]\n"
            "- Uso previsto: [¿Esta configuración sirve para lo que pidió el usuario?]\n\n"
            "PROBLEMAS DETECTADOS: [Lista los problemas concretos, o 'Ninguno' si todo está bien]\n\n"
            "RECOMENDACIÓN: [Qué cambiarías si algo está mal, o 'Configuración aprobada' si todo está bien]"
        ),
        expected_output="Revisión estructurada con veredicto, análisis y recomendaciones.",
        agent=agente_revisor,
    )

    crew_rev = Crew(agents=[agente_revisor], tasks=[tarea_revision], process=Process.sequential, verbose=False)
    resultado = str(crew_rev.kickoff()).strip()

    # Determinar veredicte positiu o negatiu
    es_correcto = "VEREDICTO: :white_check_mark:" in resultado or "CORRECTO" in resultado.upper()

    print(f"\n{'═'*60}")
    print(f"  :mag_right: REVISIÓN DE LA CONFIGURACIÓN")
    print(f"{'═'*60}")
    print(resultado)
    print(f"{'═'*60}")

    if es_correcto:
        print("\n  :white_check_mark: La configuración ha superado la revisión. ¡Lista para comprar!")
    else:
        print("\n  :x: La configuración tiene problemas. Revisa las recomendaciones anteriores.")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print("\n" + "═"*60)
    print("        PC BUILDER IA — Powered by CrewAI + Ollama")
    print("═"*60)
    print("Buscará en Reddit y tiendas para recomendarte componentes.\n")

    descripcion = input("Describe el PC que quieres:\n→ ").strip()
    if not descripcion:
        print(" Descripción vacía.")
        return

    presupuesto = input("\nPresupuesto total (ej: 1200 euros):\n→ ").strip()
    if not presupuesto:
        print(" Presupuesto vacío.")
        return

    presupuesto_num = parse_price(presupuesto)
    if presupuesto_num == 0:
        presupuesto_num = 1200.0

    print(f"\n Iniciando para: '{descripcion}' — {presupuesto_num:.0f}€\n")

    # Paso 1: Reddit
    reddit_context = step_reddit(descripcion, presupuesto)

    # Paso 2: planificaccó pressupost
    components = step_plan_components(reddit_context, descripcion, presupuesto)

    # Paso 3: scrapping part + selección validacio
    selected = step_select_products(components, presupuesto_num)

    # RESULTAT FINAL
    print("\n\n" + "═"*60)
    print("               CONFIGURACIÓN FINAL RECOMENDADA")
    print("═"*60)

    total = 0.0
    for item in selected:
        cat = item.get("categoria", "?")
        budget_max = presupuesto_num * BUDGET_DISTRIBUTION.get(cat, 0.10)
        price_val = parse_price(item.get("precio", "0"))
        over = " " if price_val > budget_max else " ✓"
        print(f"\n {cat.upper()}")
        print(f"    {item['nombre']}")
        print(f"    {item['precio']}{over}  (presupuesto: {budget_max:.0f}€)")
        print(f"    {item['tienda']}")
        print(f"    {item['url']}")
        total += price_val

    print(f"\n{'═'*60}")
    diff = presupuesto_num - total
    status = f" Dentro del presupuesto (sobran {diff:.0f}€)" if diff >= 0 else f"  Sobre presupuesto en {abs(diff):.0f}€"
    print(f"   TOTAL: {total:.0f}€  /  Presupuesto: {presupuesto_num:.0f}€")
    print(f"  {status}")
    print("═"*60)
    step_review(selected, presupuesto_num, descripcion)


if __name__ == "__main__":
    main()
