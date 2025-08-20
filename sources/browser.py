from __future__ import annotations

import os
import re
import ssl
import sys
import time
import uuid
import shutil
import random
import tempfile
from typing import List, Optional, Tuple, Dict

from bs4 import BeautifulSoup
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    ElementClickInterceptedException,
)
from selenium.webdriver.common.action_chains import ActionChains

from selenium_stealth import stealth
import undetected_chromedriver as uc
import chromedriver_autoinstaller
import markdownify

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sources.utility import pretty_print
from sources.logger import Logger


# ---------------------------
# Chrome executable discovery
# ---------------------------
def get_chrome_path() -> Optional[str]:
    """Locate Google Chrome/Chromium binary in a cross-platform way."""
    if sys.platform.startswith("win"):
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform.startswith("darwin"):
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        ]
    else:  # Linux
        paths = [
            "/usr/bin/google-chrome",
            "/opt/chrome/chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/local/bin/chrome",
            "/opt/google/chrome/chrome-headless-shell",
        ]

    env_path = os.environ.get("CHROME_EXECUTABLE_PATH")
    if env_path and os.path.exists(env_path) and os.access(env_path, os.X_OK):
        return env_path

    for p in paths:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p

    # не спрашиваем через input() — это ломает автоматический запуск
    return None


# ---------------------------
# User-Agent helpers
# ---------------------------
def get_random_user_agent() -> Dict[str, str]:
    """Pick a realistic desktop UA and an aligned vendor string."""
    candidates = [
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "vendor": "Google Inc.",
            "platform": "Win64",
        },
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "vendor": "Google Inc.",  # для Chrome на macOS тоже обычно Google Inc.
            "platform": "MacIntel",
        },
        {
            "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "vendor": "Google Inc.",
            "platform": "Linux x86_64",
        },
    ]
    return random.choice(candidates)


# ---------------------------
# Chromedriver setup
# ---------------------------
def install_chromedriver() -> str:
    """
    Ensure ChromeDriver is available and return its path.
    Priority:
      1) ./chromedriver in project root (executable)
      2) chromedriver from PATH
      3) Docker fixed path
      4) Auto-installer
    """
    project_root_chromedriver = os.path.abspath("./chromedriver")
    if os.path.exists(project_root_chromedriver) and os.access(project_root_chromedriver, os.X_OK):
        print(f"Using ChromeDriver from project root: {project_root_chromedriver}")
        return project_root_chromedriver

    found = shutil.which("chromedriver")
    if found:
        return found

    if os.path.exists("/.dockerenv"):
        docker_chromedriver = "/usr/local/bin/chromedriver"
        if os.path.exists(docker_chromedriver) and os.access(docker_chromedriver, os.X_OK):
            print(f"Using Docker ChromeDriver at {docker_chromedriver}")
            return docker_chromedriver

    # fallback: autoinstall
    try:
        print("ChromeDriver not found, attempting to install automatically...")
        path = chromedriver_autoinstaller.install()
    except Exception as e:
        raise FileNotFoundError(
            "ChromeDriver not found and auto-install failed. "
            "Install it manually (https://chromedriver.chromium.org/downloads) "
            "or add it to PATH. See README if your Chrome version is >115."
        ) from e

    if not path:
        raise FileNotFoundError("ChromeDriver not found. Please install it or add it to your PATH.")
    return path


def bypass_ssl() -> None:
    """Fallback for some environments where SSL verification breaks."""
    pretty_print(
        "Bypassing SSL verification issues; we strongly advise updating your certifi bundle.",
        color="warning",
    )
    ssl._create_default_https_context = ssl._create_unverified_context


def create_undetected_chromedriver(service: Service, chrome_options: Options) -> webdriver.Chrome:
    """Create a UC driver, retrying with SSL bypass if needed."""
    try:
        driver = uc.Chrome(service=service, options=chrome_options)
    except Exception as e:
        pretty_print(f"Failed to create Chrome driver: {str(e)}. Trying to bypass SSL...", color="failure")
        try:
            bypass_ssl()
            driver = uc.Chrome(service=service, options=chrome_options)
        except Exception as e2:
            pretty_print(f"Failed to create Chrome driver, fallback failed:\n{str(e2)}.", color="failure")
            raise
    # hide webdriver flag
    try:
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    except Exception:
        pass
    return driver


# ---------------------------
# WebDriver factory
# ---------------------------
def create_driver(
    headless: bool = False,
    stealth_mode: bool = True,
    crx_path: str = "./crx/nopecha.crx",
    lang: str = "en",
) -> webdriver.Chrome:
    """Create a configured Chrome WebDriver with safe temp profile."""

    if not headless and os.path.exists("/.dockerenv"):
        print("[WARNING] Running non-headless browser in Docker may fail.")
        print("[WARNING] Consider setting headless=True in config.ini")

    chrome_path = get_chrome_path()
    if not chrome_path:
        raise FileNotFoundError(
            "Google Chrome not found. Install Chrome or set CHROME_EXECUTABLE_PATH."
        )

    chrome_options = Options()
    chrome_options.binary_location = chrome_path

    # --- базовые аргументы ---
    if headless:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-webgl")

    # --- уникальный временный профиль ---
    profile_dir = tempfile.mkdtemp(prefix="chrome_profile_")
    os.makedirs(profile_dir, exist_ok=True)
    ua = get_random_user_agent()
    width, height = (1920, 1080)

    chrome_options.add_argument(f"--user-data-dir={profile_dir}")
    chrome_options.add_argument("--profile-directory=Default")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(f"--accept-lang={lang}-{lang.upper()},{lang};q=0.9")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-features=TranslateUI")
    chrome_options.add_argument("--disable-ipc-flooding-protection")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--autoplay-policy=user-gesture-required")
    chrome_options.add_argument("--disable-features=SitePerProcess,IsolateOrigins")
    chrome_options.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument(f"user-agent={ua['ua']}")
    chrome_options.add_argument(f"--window-size={width},{height}")

    # уникальный порт
    debug_port = 5000 + (os.getpid() % 1000)
    chrome_options.add_argument(f"--remote-debugging-port={debug_port}")

    print(f"[DEBUG] Starting Chrome with profile: {profile_dir}, port: {debug_port}")

    # --- расширение NopeCHA ---
    if not stealth_mode and os.path.exists(crx_path):
        chrome_options.add_extension(crx_path)

    # --- драйвер ---
    chromedriver_path = install_chromedriver()
    service = Service(chromedriver_path)

    if stealth_mode:
        driver = uc.Chrome(service=service, options=chrome_options)
        try:
            stealth(
                driver,
                languages=[f"{lang}-{lang.upper()}", lang],
                vendor=ua.get("vendor", "Google Inc."),
                platform=ua.get("platform", "Win64"),
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL",
                fix_hairline=True,
            )
        except Exception as e:
            print(f"[WARNING] Failed to apply stealth: {e}")
        return driver

    else:
        return webdriver.Chrome(service=service, options=chrome_options)

# ---------------------------
# Browser wrapper
# ---------------------------
class Browser:
    def __init__(self, driver: webdriver.Chrome, anticaptcha_manual_install: bool = False):
        """Initialize the browser with optional AntiCaptcha manual installation."""
        self.js_scripts_folder = (
            "./sources/web_scripts/" if __name__ != "__main__" else "./web_scripts/"
        )
        self.anticaptcha = (
            "https://chrome.google.com/webstore/detail/nopecha-captcha-solver/"
            "dknlfmjaanfblgfdfebhijalfmhmjjjo/related"
        )
        self.logger = Logger("browser.log")
        self.screenshot_folder = os.path.join(os.getcwd(), ".screenshots")
        self.tabs: List[str] = []
        self.driver = driver
        self.wait = WebDriverWait(self.driver, 10)

        self.setup_tabs()
        self.patch_browser_fingerprint()
        if anticaptcha_manual_install:
            self.load_anticaptcha_manually()

    def setup_tabs(self) -> None:
        self.tabs = self.driver.window_handles
        try:
            self.driver.get("https://www.google.com")
        except Exception as e:
            self.logger.log(f"Failed to setup initial tab: {str(e)}")
        self.screenshot()

    def switch_control_tab(self) -> None:
        self.logger.log("Switching to control tab.")
        if self.tabs:
            self.driver.switch_to.window(self.tabs[0])

    def load_anticaptcha_manually(self) -> None:
        pretty_print("You might want to install the AntiCaptcha extension for captchas.", color="warning")
        try:
            self.driver.get(self.anticaptcha)
        except Exception as e:
            self.logger.log(f"Failed to open anticaptcha page: {str(e)}")

    def human_move(self, element) -> None:
        actions = ActionChains(self.driver)
        x_offset = random.randint(-5, 5)
        for _ in range(random.randint(2, 5)):
            actions.move_by_offset(x_offset, random.randint(-2, 2))
            actions.pause(random.uniform(0.1, 0.3))
        try:
            actions.click(element).perform()
        except Exception:
            # fallback — клик напрямую
            try:
                element.click()
            except Exception:
                pass

    def human_scroll(self) -> None:
        for _ in range(random.randint(1, 3)):
            scroll_pixels = random.randint(150, 1200)
            self.driver.execute_script(f"window.scrollBy(0, {scroll_pixels});")
            time.sleep(random.uniform(0.5, 2.0))
            if random.random() < 0.4:
                self.driver.execute_script(f"window.scrollBy(0, -{random.randint(50, 300)});")
                time.sleep(random.uniform(0.3, 1.0))

    def patch_browser_fingerprint(self) -> None:
        try:
            script = self.load_js("spoofing.js")
            self.driver.execute_script(script)
        except Exception as e:
            self.logger.log(f"Fingerprint patch skipped: {str(e)}")

    # ---------------
    # Navigation / IO
    # ---------------
    def go_to(self, url: str) -> bool:
        """Navigate to a specified URL."""
        time.sleep(random.uniform(0.4, 2.5))
        try:
            self.driver.get(url)
            time.sleep(random.uniform(0.05, 0.3))
            try:
                wait = WebDriverWait(self.driver, timeout=10)
                wait.until(
                    lambda d: not any(
                        k in d.page_source.lower() for k in ["checking your browser", "captcha"]
                    ),
                    message="stuck on 'checking browser' or verification screen",
                )
            except TimeoutException:
                self.logger.warning("Timeout while waiting to bypass 'checking your browser'")

            self.apply_web_safety()
            time.sleep(random.uniform(0.05, 0.2))
            self.human_scroll()
            self.logger.log(f"Navigated to: {url}")
            return True
        except TimeoutException as e:
            self.logger.error(f"Timeout waiting for {url} to load: {str(e)}")
            return False
        except WebDriverException as e:
            self.logger.error(f"Error navigating to {url}: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"Fatal error with go_to method on {url}:\n{str(e)}")
            raise

    def is_sentence(self, text: str) -> bool:
        """Heuristic: meaningful line of text or contains codes/digits."""
        text = text.strip()
        if any(c.isdigit() for c in text):
            return True
        words = re.findall(r"\w+", text, re.UNICODE)
        word_count = len(words)
        has_punct = any(text.endswith(p) for p in [".", "，", ",", "!", "?", "。", "！", "？", "।", "۔"])
        is_long_enough = word_count > 4
        return word_count >= 5 and (has_punct or is_long_enough)

    def get_text(self) -> Optional[str]:
        """Get page text as formatted Markdown (trimmed to 32K)."""
        try:
            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            for el in soup(["script", "style", "noscript", "meta", "link"]):
                el.decompose()
            md = markdownify.MarkdownConverter(
                heading_style="ATX",
                strip=["a"],
                autolinks=False,
                bullets="•",
                strong_em_symbol="*",
                default_title=False,
            ).convert(str(soup.body))
            lines = []
            for line in md.splitlines():
                stripped = line.strip()
                if stripped and self.is_sentence(stripped):
                    lines.append(" ".join(stripped.split()))
            result = "[Start of page]\n\n" + "\n\n".join(lines) + "\n\n[End of page]"
            result = re.sub(r"!\[(.*?)\]\(.*?\)", r"[IMAGE: \1]", result)
            self.logger.info(f"Extracted text: {result[:100]}...")
            self.logger.info(f"Extracted text length: {len(result)}")
            return result[:32768]
        except Exception as e:
            self.logger.error(f"Error getting text: {str(e)}")
            return None

    # ---------------
    # Links / URLs
    # ---------------
    def clean_url(self, url: str) -> str:
        """Strip anchors and most tracking params; keep only essential ones."""
        clean = url.split("#")[0]
        parts = clean.split("?", 1)
        base = parts[0]
        if len(parts) > 1:
            query = parts[1]
            essential = []
            for param in query.split("&"):
                if param.startswith(("q=", "s=", "_skw=")):
                    essential.append(param)
                elif param.startswith("_") or param.startswith("hash=") or param.startswith("itmmeta="):
                    break
            if essential:
                return f"{base}?{'&'.join(essential)}"
        return base

    def is_link_valid(self, url: str) -> bool:
        """Heuristic filter for navigable links."""
        if len(url) > 250:  # дал меньше 72 — слишком агрессивно
            self.logger.warning(f"URL too long: {url[:120]}...")
            return False
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            self.logger.warning(f"Invalid URL: {url}")
            return False
        if re.search(r"/\d+$", parsed.path):
            return False
        bad_ext = [
            ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp",
            ".ico", ".xml", ".json", ".rss", ".atom",
        ]
        if any(url.lower().endswith(ext) for ext in bad_ext):
            return False
        return True

    def get_navigable(self) -> List[str]:
        """Collect navigable <a> links on the page."""
        try:
            links = []
            for el in self.driver.find_elements(By.TAG_NAME, "a"):
                href = el.get_attribute("href")
                if href and href.startswith(("http", "https")):
                    links.append(
                        {"url": href, "text": el.text.strip(), "is_displayed": el.is_displayed()}
                    )
            self.logger.info(f"Found {len(links)} navigable links")
            return [
                self.clean_url(l["url"])
                for l in links
                if l["is_displayed"] and self.is_link_valid(l["url"])
            ]
        except Exception as e:
            self.logger.error(f"Error getting navigable links: {str(e)}")
            return []

    # ---------------
    # Elements / Forms
    # ---------------
    def click_element(self, xpath: str) -> bool:
        """Click an element specified by XPath."""
        try:
            element = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            if not element.is_displayed() or not element.is_enabled():
                return False
            try:
                self.logger.error("Scrolling to element for click_element.")
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", element
                )
                time.sleep(0.1)
                element.click()
                self.logger.info(f"Clicked element at {xpath}")
                return True
            except ElementClickInterceptedException as e:
                self.logger.error(f"Error click_element: {str(e)}")
                return False
        except TimeoutException:
            self.logger.warning("Timeout clicking element.")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error clicking element at {xpath}: {str(e)}")
            return False

    def load_js(self, file_name: str) -> str:
        """Load a JS helper from the scripts folder."""
        path = os.path.join(self.js_scripts_folder, file_name)
        self.logger.info(f"Loading js at {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError as e:
            raise Exception(f"Could not find: {path}") from e

    def find_all_inputs(self, timeout: int = 3):
        """Return a list of input descriptors from injected JS (see web_scripts/find_inputs.js)."""
        try:
            WebDriverWait(self.driver, timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except Exception as e:
            self.logger.error(f"Error waiting for input element: {str(e)}")
            return []
        time.sleep(0.5)
        script = self.load_js("find_inputs.js")
        try:
            return self.driver.execute_script(script) or []
        except Exception as e:
            self.logger.error(f"Error executing find_inputs.js: {str(e)}")
            return []

    def get_form_inputs(self) -> List[str]:
        """Return simplified descriptors [name]("") for visible inputs."""
        try:
            input_elements = self.find_all_inputs()
            if not input_elements:
                self.logger.info("No input element on page.")
                return ["No input forms found on the page."]

            forms: List[str] = []
            for el in input_elements:
                # ожидаем словарь, собираемый find_inputs.js
                input_type = (el.get("type") or "text").lower()
                displayed = bool(el.get("displayed", True))
                if input_type in {"hidden", "submit", "button", "image"} or not displayed:
                    continue
                input_name = el.get("text") or el.get("id") or input_type
                if input_type in {"checkbox", "radio"}:
                    # просто метим чекбоксы/радио без значения
                    forms.append(f"[{input_name}](unchecked)")
                else:
                    forms.append(f"[{input_name}](\"\")")
            return forms
        except Exception as e:
            self.logger.error(f"Error collecting form inputs: {str(e)}")
            return []

    def get_buttons_xpath(self) -> List[Tuple[str, str]]:
        """Find buttons and return (normalized_text, xpath)."""
        buttons = self.driver.find_elements(By.TAG_NAME, "button") + \
                  self.driver.find_elements(By.XPATH, "//input[@type='submit']")
        result: List[Tuple[str, str]] = []
        for i, button in enumerate(buttons):
            if not button.is_displayed() or not button.is_enabled():
                continue
            text = (button.text or button.get_attribute("value") or "").lower().replace(" ", "")
            xpath = f"(//button | //input[@type='submit'])[{i + 1}]"
            result.append((text, xpath))
        result.sort(key=lambda x: len(x[0]))
        return result

    def wait_for_submission_outcome(self, timeout: int = 10) -> bool:
        """Wait for a submission outcome (URL change or 'success' text)."""
        try:
            self.logger.info("Waiting for submission outcome...")
            current_url = self.driver.current_url
            wait = WebDriverWait(self.driver, timeout)
            wait.until(lambda d: d.current_url != current_url or d.find_elements(By.XPATH, "//*[contains(translate(text(),'SUCCESS','success'),'success')]"))
            self.logger.info("Detected submission outcome")
            return True
        except TimeoutException:
            self.logger.warning("No submission outcome detected")
            return False

    def find_and_click_btn(self, btn_type: str = "login", timeout: int = 5) -> bool:
        """Find and click a submit button matching the specified type."""
        buttons = self.get_buttons_xpath()
        if not buttons:
            self.logger.warning("No visible buttons found")
            return False

        for button_text, xpath in buttons:
            if btn_type.lower() in button_text or btn_type.lower() in xpath.lower():
                try:
                    WebDriverWait(self.driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                    if self.click_element(xpath):
                        self.logger.info(f"Clicked button '{button_text}' at XPath: {xpath}")
                        return True
                    self.logger.warning(f"Button '{button_text}' at XPath: {xpath} not clickable")
                    return False
                except TimeoutException:
                    self.logger.warning(f"Timeout waiting for '{button_text}' button at XPath: {xpath}")
                    return False
                except Exception as e:
                    self.logger.error(f"Error clicking button '{button_text}' at XPath: {xpath} - {str(e)}")
                    return False

        self.logger.warning(f"No button matching '{btn_type}' found")
        return False

    def tick_all_checkboxes(self) -> bool:
        """Tick all visible checkboxes on the page."""
        try:
            checkboxes = self.driver.find_elements(By.XPATH, "//input[@type='checkbox']")
            if not checkboxes:
                self.logger.info("No checkboxes found on the page")
                return True

            for index, checkbox in enumerate(checkboxes, 1):
                try:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable(checkbox))
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", checkbox
                    )
                    if not checkbox.is_selected():
                        try:
                            checkbox.click()
                            self.logger.info(f"Ticked checkbox {index}")
                        except ElementClickInterceptedException:
                            self.driver.execute_script("arguments[0].click();", checkbox)
                            self.logger.warning(f"Click checkbox {index} intercepted")
                    else:
                        self.logger.info(f"Checkbox {index} already ticked")
                except TimeoutException:
                    self.logger.warning(f"Timeout waiting for checkbox {index} to be clickable")
                except Exception as e:
                    self.logger.error(f"Error ticking checkbox {index}: {str(e)}")
            return True
        except Exception as e:
            self.logger.error(f"Error finding checkboxes: {str(e)}")
            return False

    def find_and_click_submission(self, timeout: int = 10) -> bool:
        candidates = [
            "login", "submit", "register", "continue", "apply", "ok", "confirm",
            "proceed", "accept", "done", "finish", "start", "calculate",
        ]
        for label in candidates:
            if self.find_and_click_btn(label, timeout):
                self.logger.info(f"Clicked on submission button: {label}")
                return True
        self.logger.warning("No submission button found")
        return False

    def find_input_xpath_by_name(self, inputs: List[Dict], name: str) -> Optional[str]:
        for field in inputs:
            if name and name in (field.get("text") or ""):
                return field.get("xpath")
        return None

    def fill_form_inputs(self, input_list: List[str]) -> bool:
        """Fill inputs from a list of strings like: [Field Name](value)."""
        if not isinstance(input_list, list):
            self.logger.error("input_list must be a list")
            return False

        inputs = self.find_all_inputs()
        try:
            for item in input_list:
                m = re.match(r"\[(.*?)\]\((.*?)\)", item)
                if not m:
                    self.logger.warning(f"Invalid format for input: {item}")
                    continue

                name, value = m.groups()
                name, value = name.strip(), value.strip()
                xpath = self.find_input_xpath_by_name(inputs, name)
                if not xpath:
                    self.logger.warning(f"Input field '{name}' not found")
                    continue

                try:
                    element = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                except TimeoutException:
                    self.logger.error(f"Timeout waiting for element '{name}' to be clickable")
                    continue

                self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
                if not element.is_displayed() or not element.is_enabled():
                    self.logger.warning(f"Element '{name}' is not interactable (not displayed or disabled)")
                    continue

                input_type = (element.get_attribute("type") or "text").lower()
                if input_type in {"checkbox", "radio"}:
                    should_be_checked = value.lower() == "checked"
                    if element.is_selected() != should_be_checked:
                        element.click()
                        self.logger.info(f"Set {name} to {value}")
                else:
                    try:
                        element.clear()
                    except Exception:
                        # некоторые поля не поддерживают clear()
                        self.driver.execute_script("arguments[0].value = '';", element)
                    element.send_keys(value)
                    self.logger.info(f"Filled {name} with {value}")
            return True
        except Exception as e:
            self.logger.error(f"Error filling form inputs: {str(e)}")
            return False

    def fill_form(self, input_list: List[str]) -> bool:
        """Fill form inputs and try to submit."""
        if not isinstance(input_list, list):
            self.logger.error("input_list must be a list")
            return False
        if self.fill_form_inputs(input_list):
            self.logger.info("Form filled successfully")
            self.tick_all_checkboxes()
            if self.find_and_click_submission():
                if self.wait_for_submission_outcome():
                    self.logger.info("Submission outcome detected")
                    return True
                self.logger.warning("No submission outcome detected")
            else:
                self.logger.warning("Failed to submit form")
        self.logger.warning("Failed to fill form inputs")
        return False

    # ---------------
    # Misc / utils
    # ---------------
    def get_current_url(self) -> str:
        return self.driver.current_url

    def get_page_title(self) -> str:
        return self.driver.title

    def scroll_bottom(self) -> bool:
        try:
            self.logger.info("Scrolling to the bottom of the page...")
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.5)
            return True
        except Exception as e:
            self.logger.error(f"Error scrolling: {str(e)}")
            return False

    def get_screenshot(self) -> str:
        return os.path.join(self.screenshot_folder, "updated_screen.png")

    def screenshot(self, filename: str = "updated_screen.png") -> bool:
        """Try to capture a full-page screenshot by zooming out a little."""
        self.logger.info("Taking full page screenshot...")
        time.sleep(0.1)
        try:
            self.driver.execute_script("document.body.style.zoom='75%'")
            time.sleep(0.1)
            path = os.path.join(self.screenshot_folder, filename)
            os.makedirs(self.screenshot_folder, exist_ok=True)
            self.driver.save_screenshot(path)
            self.logger.info(f"Full page screenshot saved as {filename}")
        except Exception as e:
            self.logger.error(f"Error taking full page screenshot: {str(e)}")
            return False
        finally:
            try:
                self.driver.execute_script("document.body.style.zoom='1'")
            except Exception:
                pass
        return True

    def apply_web_safety(self) -> None:
        """Inject a JS guard to disable annoying/malicious behaviors."""
        self.logger.info("Applying web safety measures...")
        try:
            script = self.load_js("inject_safety_script.js")
            self.driver.execute_script(script)
        except Exception as e:
            self.logger.error(f"Error applying web safety script: {str(e)}")


# ---------------------------
# Manual local test
# ---------------------------
if __name__ == "__main__":
    driver = create_driver(headless=False, stealth_mode=True, crx_path="../crx/nopecha.crx")
    browser = Browser(driver, anticaptcha_manual_install=True)

    input("press enter to continue")
    print("AntiCaptcha / Form Test")
    browser.go_to("https://bot.sannysoft.com")
    time.sleep(5)
    browser.go_to("https://home.openweathermap.org/users/sign_up")
    inputs_visible = browser.get_form_inputs()
    print("inputs:", inputs_visible)
    input("press enter to exit")

# Test sites for browser fingerprinting and captcha
# https://nowsecure.nl/
# https://bot.sannysoft.com
# https://browserleaks.com/
# https://bot.incolumitas.com/
# https://fingerprintjs.github.io/fingerprintjs/
# https://antoinevastel.com/bots/
