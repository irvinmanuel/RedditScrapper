# pc_builder_agent.py
# Requiere: pip install crewai ollama playwright playwright-stealth beautifulsoup4 requests selenium
# Y: playwright install chromium

import asyncio
import json
import requests
import urllib.parse
import time
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
from ollama import Client

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN OLLAMA
# ─────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434"
LLM_MODEL = "ollama/qwen3.5"  # Ajusta si usas otro modelo

# ─────────────────────────────────────────────────────────────
# HELPERS: SELENIUM DRIVER (para Reddit via DuckDuckGo)
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


def search_reddit_urls(query: str, max_results: int = 5) -> list:
    """Busca posts de Reddit en DuckDuckGo y devuelve lista de URLs."""
    driver = get_selenium_driver()
    links = []
    try:
        full_query = urllib.parse.urlencode({"q": f"{query} site:reddit.com", "kl": "us-en"})
        url = f"https://www.duckduckgo.com/?{full_query}"
        driver.get(url)
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
    finally:
        driver.quit()
    return links


def scrape_reddit_post(reddit_url: str) -> str:
    """Scrappea un post de Reddit en formato JSON y devuelve comentarios."""
    url = reddit_url if reddit_url.endswith(".json") else reddit_url.rstrip("/") + ".json"
    headers = {"User-Agent": "Mozilla/5.0 (PCBuilder Bot)"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        post = data[0]["data"]["children"][0]["data"]
        title = post.get("title", "")
        comments = data[1]["data"]["children"]
        sorted_comments = sorted(
            [c for c in comments if c["kind"] == "t1"],
            key=lambda x: x["data"].get("score", 0),
            reverse=True,
        )
        result = f"POST: {title}\n\nCOMENTARIOS TOP:\n"
        for c in sorted_comments[:10]:
            body = c["data"].get("body", "")
            score = c["data"].get("score", 0)
            result += f"[{score} votos] {body}\n---\n"
        return result
    except Exception as e:
        return f"Error scrapeando {reddit_url}: {e}"


# ─────────────────────────────────────────────────────────────
# HELPERS: PLAYWRIGHT SCRAPERS (Amazon, PcComponentes, Wallapop)
# ─────────────────────────────────────────────────────────────
async def _scrape_amazon(page, query: str) -> list:
    url = f"https://www.amazon.com/s?k={quote_plus(query)}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    soup = BeautifulSoup(await page.content(), "html.parser")
    products = []
    for item in soup.select("div[data-component-type='s-search-result']"):
        if item.select_one("span.puis-sponsored-label-text"):
            continue
        title_el = item.select_one("h2 span")
        title = title_el.get_text(strip=True) if title_el else "N/A"
        link_el = item.select_one("h2 a")
        href = link_el["href"] if link_el and link_el.get("href") else None
        product_url = f"https://www.amazon.com{href}" if href and href.startswith("/") else (href or "N/A")
        price_el = item.select_one("span.a-price span.a-offscreen")
        price = price_el.get_text(strip=True) if price_el else "N/A"
        rating_el = item.select_one("span.a-icon-alt")
        rating = rating_el.get_text(strip=True).split(" ")[0] if rating_el else "N/A"
        if title != "N/A" and price != "N/A":
            products.append({"source": "Amazon", "title": title, "price": price, "rating": rating, "url": product_url})
    return products[:5]


async def _scrape_pccomponentes(page, query: str) -> list:
    search_url = f"https://www.pccomponentes.com/search?query={quote_plus(query)}&sort=sales"
    api_url = f"https://www.pccomponentes.com/api/articles/search-list?url={quote_plus(search_url)}"
    await page.goto(
        f"https://www.pccomponentes.com/search/?query={quote_plus(query)}",
        wait_until="domcontentloaded",
        timeout=60000,
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
    for art in articles[:5]:
        name = art.get("name", "N/A")
        price = art.get("promotionalPrice") or art.get("price")
        final_price = f"{price} €" if price else "N/A"
        slug = art.get("slug", "")
        url = f"https://www.pccomponentes.com/{slug}" if slug else "N/A"
        rating = str(art.get("ratingAvg", "N/A"))
        products.append({"source": "PcComponentes", "title": name, "price": final_price, "rating": rating, "url": url})
    return products


async def _scrape_wallapop(page, query: str) -> list:
    import uuid
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
    for item in items[:5]:
        title = item.get("title", "N/A")
        price_obj = item.get("price", {})
        amount = price_obj.get("amount")
        price = f"{amount} EUR" if amount is not None else "N/A"
        web_slug = item.get("web_slug", "")
        url = f"https://es.wallapop.com/item/{web_slug}" if web_slug else "N/A"
        products.append({"source": "Wallapop", "title": title, "price": price, "rating": "N/A", "url": url})
    return products


async def scrape_all_stores(query: str) -> list:
    """Scrappea Amazon, PcComponentes y Wallapop para un componente."""
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
        amazon = await _scrape_amazon(page, query)
        pcc = await _scrape_pccomponentes(page, query)
        wallapop = await _scrape_wallapop(page, query)
        await browser.close()
    return amazon + pcc + wallapop


# ─────────────────────────────────────────────────────────────
# LLM DIRECTO VIA OLLAMA (para llamadas fuera de CrewAI)
# ─────────────────────────────────────────────────────────────
def ask_llm(prompt: str) -> str:
    """Llama directamente al LLM local via Ollama."""
    client = Client(host=OLLAMA_BASE_URL)
    response = client.chat(
        model="qwen3.5",
        messages=[{"role": "user", "content": prompt}],
    )
    return response["message"]["content"]


# ─────────────────────────────────────────────────────────────
# CREWAI TOOLS
# ─────────────────────────────────────────────────────────────
@tool("buscar_posts_reddit")
def tool_buscar_reddit(query: str) -> str:
    """
    Busca posts relevantes en Reddit sobre configuraciones de PC.
    Recibe una query de búsqueda y devuelve URLs y snippets de posts.
    """
    print(f"\n🔎 Buscando en Reddit: '{query}'...")
    links = search_reddit_urls(query, max_results=5)
    if not links:
        return "No se encontraron posts relevantes en Reddit."
    result = f"Encontrados {len(links)} posts:\n\n"
    for i, link in enumerate(links, 1):
        result += f"{i}. {link['title']}\n   URL: {link['url']}\n   {link['snippet']}\n\n"
    return result


@tool("scrappear_post_reddit")
def tool_scrappear_reddit(url: str) -> str:
    """
    Scrappea el contenido completo de un post de Reddit dado su URL.
    Devuelve el título y los comentarios con más votos.
    """
    print(f"\n📄 Scrapeando Reddit: {url}")
    return scrape_reddit_post(url)


@tool("buscar_componente_tiendas")
def tool_buscar_tiendas(query: str) -> str:
    """
    Busca un componente de PC en Amazon, PcComponentes y Wallapop.
    Devuelve una lista de productos con nombre, precio y URL.
    Input: nombre del componente a buscar (ej: 'AMD Ryzen 5 7600X').
    """
    print(f"\n🛒 Scrapeando tiendas para: '{query}'...")
    products = asyncio.run(scrape_all_stores(query))
    if not products:
        return f"No se encontraron productos para '{query}'."
    result = f"Productos encontrados para '{query}':\n\n"
    for p in products:
        result += (
            f"[{p['source']}] {p['title']}\n"
            f"  Precio: {p['price']} | Rating: {p['rating']}\n"
            f"  URL: {p['url']}\n\n"
        )
    return result


# ─────────────────────────────────────────────────────────────
# AGENTES CREWAI
# ─────────────────────────────────────────────────────────────
agente_reddit = Agent(
    role="Investigador de Hardware en Reddit",
    goal=(
        "Buscar y analizar posts de Reddit sobre configuraciones de PC "
        "para extraer los componentes más recomendados por la comunidad "
        "dentro del presupuesto indicado."
    ),
    backstory=(
        "Eres un entusiasta del hardware con años de experiencia leyendo "
        "foros de Reddit como r/buildapc, r/pcmasterrace y r/esGaming. "
        "Sabes identificar qué combinaciones de componentes ofrecen la mejor "
        "relación calidad-precio según la comunidad."
    ),
    llm=LLM_MODEL,
    tools=[tool_buscar_reddit, tool_scrappear_reddit],
    verbose=True,
    allow_delegation=False,
)

agente_planificador = Agent(
    role="Planificador de Configuración PC",
    goal=(
        "Analizar las recomendaciones de Reddit y el presupuesto del usuario "
        "para definir una lista exacta de componentes a comprar, con modelos "
        "específicos y términos de búsqueda optimizados para cada tienda."
    ),
    backstory=(
        "Eres un experto en arquitecturas de PC con conocimiento profundo de "
        "compatibilidad entre componentes. Siempre tienes en cuenta el socket "
        "del procesador, el TDP, la compatibilidad de RAM y el factor de forma "
        "para proponer configuraciones coherentes y equilibradas."
    ),
    llm=LLM_MODEL,
    tools=[],
    verbose=True,
    allow_delegation=False,
)

agente_selector = Agent(
    role="Comparador y Selector de Productos",
    goal=(
        "Para cada componente, analizar los productos encontrados en tiendas "
        "y seleccionar el mejor en términos de precio, valoraciones y disponibilidad. "
        "Devolver siempre nombre exacto, precio y URL del producto seleccionado."
    ),
    backstory=(
        "Eres un analista de compras especializado en hardware informático. "
        "Sabes comparar productos de diferentes tiendas teniendo en cuenta "
        "precio, garantía, valoraciones y reputación del vendedor. "
        "Siempre priorizas la mejor relación calidad-precio dentro del presupuesto."
    ),
    llm=LLM_MODEL,
    tools=[tool_buscar_tiendas],
    verbose=True,
    allow_delegation=False,
)


# ─────────────────────────────────────────────────────────────
# CONSTRUCCIÓN DEL CREW Y TAREAS DINÁMICAS
# ─────────────────────────────────────────────────────────────
def build_and_run_crew(descripcion_usuario: str, presupuesto: str) -> str:
    """Construye y ejecuta el Crew completo para la petición del usuario."""

    tarea_reddit = Task(
        description=(
            f"El usuario quiere un PC con las siguientes características: '{descripcion_usuario}'. "
            f"Presupuesto total: {presupuesto}.\n\n"
            "1. Busca en Reddit posts relevantes sobre configuraciones de PC con este presupuesto "
            "(usa queries como 'build PC {presupuesto} euros 2024' o 'mejor PC gaming {presupuesto}').\n"
            "2. Scrappea al menos 3 posts de los resultados encontrados.\n"
            "3. Extrae y resume qué componentes recomienda la comunidad (CPU, GPU, RAM, "
            "placa base, almacenamiento, fuente, caja).\n"
            "4. Indica para cada componente el modelo más mencionado o recomendado."
        ),
        expected_output=(
            "Un resumen estructurado con los componentes recomendados por Reddit, "
            "incluyendo modelos específicos para cada categoría: CPU, GPU, RAM, "
            "Placa Base, Almacenamiento (SSD/HDD), Fuente de Alimentación y Caja/Chasis."
        ),
        agent=agente_reddit,
    )

    tarea_planificacion = Task(
        description=(
            f"Basándote en las recomendaciones de Reddit para el PC de '{descripcion_usuario}' "
            f"con presupuesto de {presupuesto}, crea una lista definitiva de componentes a comprar.\n\n"
            "Para cada componente proporciona:\n"
            "- Categoría (CPU, GPU, RAM, etc.)\n"
            "- Modelo recomendado (específico y exacto)\n"
            "- Query de búsqueda optimizada para encontrarlo en tiendas online españolas\n"
            "- Precio objetivo estimado\n\n"
            "Verifica la compatibilidad entre todos los componentes antes de finalizar la lista. "
            "Formato de salida: JSON con lista de componentes."
        ),
        expected_output=(
            "Un JSON válido con la estructura: "
            '[{"categoria": "CPU", "modelo": "AMD Ryzen 5 7600X", "query_busqueda": "Ryzen 5 7600X", "precio_objetivo": "220€"}, ...]'
            " incluyendo todos los componentes necesarios para el PC."
        ),
        agent=agente_planificador,
        context=[tarea_reddit],
    )

    tarea_seleccion = Task(
        description=(
            "Para cada componente de la lista del planificador, usa la herramienta 'buscar_componente_tiendas' "
            "para encontrar productos en Amazon, PcComponentes y Wallapop.\n\n"
            "Por cada componente:\n"
            "1. Llama a la herramienta con el 'query_busqueda' del componente\n"
            "2. Analiza los resultados obtenidos\n"
            "3. Selecciona el mejor producto considerando precio, rating y disponibilidad\n"
            "4. Registra: nombre del producto, precio, tienda y URL\n\n"
            f"El presupuesto total es {presupuesto}. Intenta no superarlo con la suma de todos los componentes."
        ),
        expected_output=(
            "Una lista final y completa de todos los componentes seleccionados con este formato:\n\n"
            "🖥️ CONFIGURACIÓN PC RECOMENDADA\n"
            "═══════════════════════════════\n"
            "✅ CPU: [nombre del producto]\n"
            "   💰 Precio: [precio]\n"
            "   🏪 Tienda: [tienda]\n"
            "   🔗 URL: [url]\n\n"
            "(repetir para cada componente)\n\n"
            "💶 PRESUPUESTO TOTAL ESTIMADO: [suma]\n"
            "📊 COMPARATIVA VS PRESUPUESTO: [análisis]"
        ),
        agent=agente_selector,
        context=[tarea_planificacion],
    )

    crew = Crew(
        agents=[agente_reddit, agente_planificador, agente_selector],
        tasks=[tarea_reddit, tarea_planificacion, tarea_seleccion],
        process=Process.sequential,
        verbose=True,
        memory=False,
        planning=False,
    )

    result = crew.kickoff()
    return str(result)


# ─────────────────────────────────────────────────────────────
# ENTRADA DE USUARIO POR TERMINAL
# ─────────────────────────────────────────────────────────────
def main():
    print("\n" + "═" * 60)
    print("       🤖 PC BUILDER IA — Powered by CrewAI + Ollama")
    print("═" * 60)
    print("Este agente buscará en Reddit y tiendas online para")
    print("recomendarte los mejores componentes para tu PC.\n")

    print("Describe el tipo de PC que quieres:")
    print("(Ej: 'PC gaming para jugar a 1080p, algo compacto, para Fortnite y streaming')")
    descripcion = input("→ ").strip()
    if not descripcion:
        print("❌ No has introducido una descripción.")
        return

    print("\nIndica tu presupuesto total:")
    print("(Ej: '800€', '1200 euros', '500 dólares')")
    presupuesto = input("→ ").strip()
    if not presupuesto:
        print("❌ No has introducido un presupuesto.")
        return

    print(f"\n🚀 Iniciando búsqueda para: '{descripcion}' con presupuesto {presupuesto}")
    print("Esto puede tardar varios minutos...\n")

    resultado = build_and_run_crew(descripcion, presupuesto)

    print("\n" + "═" * 60)
    print("              🎯 RESULTADO FINAL")
    print("═" * 60)
    print(resultado)
    print("═" * 60)


if __name__ == "__main__":
    main()
