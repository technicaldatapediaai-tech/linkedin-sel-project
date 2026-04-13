import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "profiles.json"
SETTINGS_FILE = BASE_DIR / "settings.json"
PROFILE_DIR = BASE_DIR / "chrome-profile"
DEBUG_DIR = BASE_DIR / "debug_output"
WAIT_SECONDS = 15
DEEP_QUERY_SCRIPT = """
const selectors = arguments[0] || [];
const labels = (arguments[1] || []).map((value) => String(value).toLowerCase());
const requireLabel = labels.length > 0;
const matches = [];
const visitedRoots = new Set();

function elementText(element) {
  return [
    element.innerText,
    element.textContent,
    element.getAttribute('aria-label'),
    element.getAttribute('placeholder'),
    element.getAttribute('value'),
    element.value,
  ]
    .filter(Boolean)
    .join(' ')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function isUsable(element) {
  if (!(element instanceof Element)) {
    return false;
  }
  const style = element.ownerDocument.defaultView.getComputedStyle(element);
  if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') {
    return false;
  }
  if (element.getAttribute('aria-hidden') === 'true') {
    return false;
  }
  if ('disabled' in element && element.disabled) {
    return false;
  }
  const rect = element.getBoundingClientRect();
  return rect.width > 0 || rect.height > 0;
}

function searchRoot(root) {
  if (!root || visitedRoots.has(root)) {
    return;
  }
  visitedRoots.add(root);

  for (const selector of selectors) {
    let elements = [];
    try {
      elements = Array.from(root.querySelectorAll(selector));
    } catch (error) {
      continue;
    }
    for (const element of elements) {
      if (!isUsable(element)) {
        continue;
      }
      if (requireLabel) {
        const content = elementText(element);
        if (!labels.some((label) => content.includes(label))) {
          continue;
        }
      }
      if (!matches.includes(element)) {
        matches.push(element);
      }
    }
  }

  let descendants = [];
  try {
    descendants = Array.from(root.querySelectorAll('*'));
  } catch (error) {
    descendants = [];
  }
  for (const element of descendants) {
    if (element.shadowRoot) {
      searchRoot(element.shadowRoot);
    }
    if (element.tagName === 'IFRAME') {
      try {
        if (element.contentDocument) {
          searchRoot(element.contentDocument);
        }
      } catch (error) {
      }
    }
  }
}

searchRoot(document);
return matches;
"""


@dataclass
class ProfileTask:
    url: str
    action: str
    note: str = ""
    message: str = ""


@dataclass
class Settings:
    attach_to_existing_browser: bool = False
    debugger_address: str = "127.0.0.1:9222"


def load_tasks(path: Path) -> list[ProfileTask]:
    raw_tasks = json.loads(path.read_text(encoding="utf-8"))
    tasks: list[ProfileTask] = []
    for item in raw_tasks:
        tasks.append(
            ProfileTask(
                url=item["url"].strip(),
                action=item["action"].strip().lower(),
                note=item.get("note", "").strip(),
                message=item.get("message", "").strip(),
            )
        )
    return tasks


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()

    raw = json.loads(path.read_text(encoding="utf-8"))
    return Settings(
        attach_to_existing_browser=bool(raw.get("attach_to_existing_browser", False)),
        debugger_address=str(raw.get("debugger_address", "127.0.0.1:9222")).strip(),
    )


def build_driver(settings: Settings) -> webdriver.Chrome:
    options = ChromeOptions()
    if settings.attach_to_existing_browser:
        print(f"Attaching to existing Chrome at {settings.debugger_address}")
        if not debugger_is_reachable(settings.debugger_address):
            raise RuntimeError(
                "Chrome debug port is not reachable. Close all Chrome windows and start Chrome with "
                f"--remote-debugging-port={settings.debugger_address.split(':')[-1]} before running this script."
            )
        options.debugger_address = settings.debugger_address
    else:
        print("Launching a new Chrome session with the local chrome-profile folder")
        options.add_argument(f"--user-data-dir={PROFILE_DIR}")
        options.add_argument("--start-maximized")
    return webdriver.Chrome(options=options)


def save_debug_artifacts(driver: webdriver.Chrome, prefix: str) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    safe_prefix = prefix.replace(" ", "_").replace("/", "_").replace("\\", "_")
    html_path = DEBUG_DIR / f"{safe_prefix}.html"
    screenshot_path = DEBUG_DIR / f"{safe_prefix}.png"
    html_path.write_text(driver.page_source, encoding="utf-8")
    driver.save_screenshot(str(screenshot_path))
    print(f"  Saved debug HTML: {html_path}")
    print(f"  Saved debug screenshot: {screenshot_path}")


def debugger_is_reachable(debugger_address: str) -> bool:
    host, port_text = debugger_address.split(":", maxsplit=1)
    try:
        port = int(port_text)
    except ValueError:
        return False

    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def wait_for_any(driver: webdriver.Chrome, selectors: list[tuple[str, str]]) -> Optional[WebElement]:
    wait = WebDriverWait(driver, WAIT_SECONDS)
    end_time = time.time() + WAIT_SECONDS

    while time.time() < end_time:
        for by, selector in selectors:
            elements = driver.find_elements(by, selector)
            for element in elements:
                if element.is_displayed() and element.is_enabled():
                    return element
        time.sleep(0.5)

    try:
        wait.until(lambda d: False)
    except TimeoutException:
        return None
    return None


def deep_find_elements(
    driver: webdriver.Chrome,
    selectors: list[str],
    labels: Optional[list[str]] = None,
) -> list[WebElement]:
    return driver.execute_script(DEEP_QUERY_SCRIPT, selectors, labels or [])


def deep_wait_for_any(
    driver: webdriver.Chrome,
    selectors: list[str],
    labels: Optional[list[str]] = None,
) -> Optional[WebElement]:
    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        elements = deep_find_elements(driver, selectors, labels)
        for element in elements:
            if element.is_enabled():
                return element
        time.sleep(0.5)
    return None


def cdp_flattened_nodes(driver: webdriver.Chrome) -> list[dict]:
    try:
        document = driver.execute_cdp_cmd("DOM.getFlattenedDocument", {"depth": -1, "pierce": True})
        return document.get("nodes", [])
    except Exception:
        return []


def cdp_node_attributes(node: dict) -> dict[str, str]:
    raw_attributes = node.get("attributes", [])
    attributes: dict[str, str] = {}
    for index in range(0, len(raw_attributes), 2):
        if index + 1 < len(raw_attributes):
            attributes[str(raw_attributes[index])] = str(raw_attributes[index + 1])
    return attributes


def cdp_find_nodes_by_attribute(
    driver: webdriver.Chrome,
    tag_names: list[str],
    attribute_name: str,
    values: list[str],
) -> list[int]:
    normalized_tags = {tag.upper() for tag in tag_names}
    normalized_values = {value.lower() for value in values}
    node_ids: list[int] = []
    for node in cdp_flattened_nodes(driver):
        if node.get("nodeName") not in normalized_tags:
            continue
        attributes = cdp_node_attributes(node)
        attribute_value = attributes.get(attribute_name)
        if attribute_value and attribute_value.lower() in normalized_values:
            node_id = node.get("nodeId")
            if isinstance(node_id, int):
                node_ids.append(node_id)
    return node_ids


def cdp_click_node(driver: webdriver.Chrome, node_id: int) -> bool:
    try:
        resolved = driver.execute_cdp_cmd("DOM.resolveNode", {"nodeId": node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not object_id:
            return False
        visibility = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function() {
                        const style = this.ownerDocument.defaultView.getComputedStyle(this);
                        const rect = this.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && this.getAttribute('aria-hidden') !== 'true'
                            && rect.width > 0
                            && rect.height > 0;
                    }
                """,
                "returnByValue": True,
            },
        )
        if not visibility.get("result", {}).get("value"):
            return False
        driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function() {
                        this.scrollIntoView({block: 'center'});
                        this.click();
                        return true;
                    }
                """,
                "returnByValue": True,
            },
        )
        return True
    except Exception:
        return False


def cdp_set_node_value(driver: webdriver.Chrome, node_id: int, value: str) -> bool:
    try:
        resolved = driver.execute_cdp_cmd("DOM.resolveNode", {"nodeId": node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not object_id:
            return False
        visibility = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function() {
                        const style = this.ownerDocument.defaultView.getComputedStyle(this);
                        const rect = this.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && this.getAttribute('aria-hidden') !== 'true'
                            && rect.width > 0
                            && rect.height > 0;
                    }
                """,
                "returnByValue": True,
            },
        )
        if not visibility.get("result", {}).get("value"):
            return False
        result = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function(value) {
                        this.focus();
                        const isTextInput = this.tagName === 'TEXTAREA' || this.tagName === 'INPUT';
                        if (isTextInput) {
                            const prototype = this.tagName === 'TEXTAREA'
                                ? HTMLTextAreaElement.prototype
                                : HTMLInputElement.prototype;
                            const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
                            if (!descriptor || !descriptor.set) {
                                return false;
                            }
                            descriptor.set.call(this, value);
                            this.dispatchEvent(new Event('input', { bubbles: true }));
                            this.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                        if (this.isContentEditable || this.getAttribute('contenteditable') === 'true' || this.getAttribute('role') === 'textbox') {
                            this.textContent = value;
                            this.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
                            this.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                        return false;
                    }
                """,
                "arguments": [{"value": value}],
                "returnByValue": True,
            },
        )
        return bool(result.get("result", {}).get("value"))
    except Exception:
        return False


def ax_tree(driver: webdriver.Chrome) -> list[dict]:
    try:
        driver.execute_cdp_cmd("Accessibility.enable", {})
    except Exception:
        pass
    try:
        return driver.execute_cdp_cmd("Accessibility.getFullAXTree", {}).get("nodes", [])
    except Exception:
        return []


def ax_find_backend_node_ids(
    driver: webdriver.Chrome,
    role_names: list[str],
    accessible_names: list[str],
) -> list[int]:
    normalized_roles = {role.lower() for role in role_names}
    normalized_names = {name.lower() for name in accessible_names}
    backend_ids: list[int] = []
    for node in ax_tree(driver):
        if node.get("ignored"):
            continue
        role = str(node.get("role", {}).get("value", "")).lower()
        name = str(node.get("name", {}).get("value", "")).strip().lower()
        if role not in normalized_roles or name not in normalized_names:
            continue
        backend_id = node.get("backendDOMNodeId")
        if isinstance(backend_id, int):
            backend_ids.append(backend_id)
    return backend_ids


def cdp_click_backend_node(driver: webdriver.Chrome, backend_node_id: int) -> bool:
    try:
        resolved = driver.execute_cdp_cmd("DOM.resolveNode", {"backendNodeId": backend_node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not object_id:
            return False
        visibility = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function() {
                        const style = this.ownerDocument.defaultView.getComputedStyle(this);
                        const rect = this.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && this.getAttribute('aria-hidden') !== 'true'
                            && rect.width > 0
                            && rect.height > 0;
                    }
                """,
                "returnByValue": True,
            },
        )
        if not visibility.get("result", {}).get("value"):
            return False
        driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function() {
                        this.scrollIntoView({block: 'center'});
                        this.click();
                        return true;
                    }
                """,
                "returnByValue": True,
            },
        )
        return True
    except Exception:
        return False


def cdp_set_backend_node_text(driver: webdriver.Chrome, backend_node_id: int, value: str) -> bool:
    try:
        resolved = driver.execute_cdp_cmd("DOM.resolveNode", {"backendNodeId": backend_node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not object_id:
            return False
        visibility = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function() {
                        const style = this.ownerDocument.defaultView.getComputedStyle(this);
                        const rect = this.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && this.getAttribute('aria-hidden') !== 'true'
                            && rect.width > 0
                            && rect.height > 0;
                    }
                """,
                "returnByValue": True,
            },
        )
        if not visibility.get("result", {}).get("value"):
            return False
        result = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function(value) {
                        this.focus();
                        const isTextInput = this.tagName === 'TEXTAREA' || this.tagName === 'INPUT';
                        if (isTextInput) {
                            const prototype = this.tagName === 'TEXTAREA'
                                ? HTMLTextAreaElement.prototype
                                : HTMLInputElement.prototype;
                            const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
                            if (!descriptor || !descriptor.set) {
                                return false;
                            }
                            descriptor.set.call(this, value);
                            this.dispatchEvent(new Event('input', { bubbles: true }));
                            this.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                        if (this.isContentEditable || this.getAttribute('contenteditable') === 'true' || this.getAttribute('role') === 'textbox') {
                            this.textContent = value;
                            this.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
                            this.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                        return false;
                    }
                """,
                "arguments": [{"value": value}],
                "returnByValue": True,
            },
        )
        return bool(result.get("result", {}).get("value"))
    except Exception:
        return False


def cdp_find_editable_node_ids(driver: webdriver.Chrome, hints: Optional[list[str]] = None) -> list[int]:
    normalized_hints = [hint.lower() for hint in (hints or [])]
    node_ids: list[int] = []
    for node in cdp_flattened_nodes(driver):
        node_name = str(node.get("nodeName", "")).upper()
        attributes = cdp_node_attributes(node)
        role = attributes.get("role", "").lower()
        contenteditable = attributes.get("contenteditable", "").lower()
        text_blob = " ".join(
            filter(
                None,
                [
                    attributes.get("aria-label"),
                    attributes.get("aria-placeholder"),
                    attributes.get("placeholder"),
                    attributes.get("name"),
                    attributes.get("id"),
                    attributes.get("maxlength"),
                ],
            )
        ).lower()
        is_editable = (
            node_name in {"TEXTAREA", "INPUT"}
            or role == "textbox"
            or contenteditable == "true"
            or attributes.get("aria-multiline", "").lower() == "true"
        )
        if not is_editable:
            continue
        if normalized_hints and not any(hint in text_blob for hint in normalized_hints):
            continue
        node_id = node.get("nodeId")
        if isinstance(node_id, int):
            node_ids.append(node_id)
    return node_ids


def ax_node_text(node: dict) -> str:
    chunks: list[str] = []
    for key in ("name", "description", "value"):
        raw = node.get(key, {})
        if isinstance(raw, dict):
            value = raw.get("value")
            if value:
                chunks.append(str(value))
    for prop in node.get("properties", []):
        if not isinstance(prop, dict):
            continue
        prop_name = str(prop.get("name", ""))
        prop_value = prop.get("value", {})
        if isinstance(prop_value, dict) and prop_value.get("value") not in (None, ""):
            chunks.append(f"{prop_name} {prop_value.get('value')}")
    return " ".join(chunks).strip().lower()


def ax_find_editable_backend_node_ids(
    driver: webdriver.Chrome,
    hints: Optional[list[str]] = None,
) -> list[int]:
    normalized_hints = [hint.lower() for hint in (hints or [])]
    backend_ids: list[int] = []
    for node in ax_tree(driver):
        if node.get("ignored"):
            continue
        role = str(node.get("role", {}).get("value", "")).lower()
        if not any(token in role for token in ["textbox", "text field", "textarea"]):
            continue
        text_blob = ax_node_text(node)
        if normalized_hints and not any(hint in text_blob for hint in normalized_hints):
            continue
        backend_id = node.get("backendDOMNodeId")
        if isinstance(backend_id, int):
            backend_ids.append(backend_id)
    return backend_ids


def element_accepts_text(element: WebElement) -> bool:
    try:
        tag_name = element.tag_name.lower()
    except Exception:
        tag_name = ""
    try:
        role = (element.get_attribute("role") or "").lower()
    except Exception:
        role = ""
    try:
        contenteditable = (element.get_attribute("contenteditable") or "").lower()
    except Exception:
        contenteditable = ""
    return tag_name in {"textarea", "input"} or role == "textbox" or contenteditable == "true"


def fill_editable_element(driver: webdriver.Chrome, element: WebElement, text: str) -> bool:
    if not element_accepts_text(element):
        return False
    try:
        click_element(driver, element)
    except Exception:
        pass
    try:
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.DELETE)
        element.send_keys(text)
        return True
    except Exception:
        pass
    try:
        return bool(
            driver.execute_script(
                """
                const element = arguments[0];
                const value = arguments[1];
                element.focus();
                if (element.tagName === 'TEXTAREA' || element.tagName === 'INPUT') {
                    const prototype = element.tagName === 'TEXTAREA'
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
                    if (!descriptor || !descriptor.set) {
                        return false;
                    }
                    descriptor.set.call(element, value);
                    element.dispatchEvent(new Event('input', { bubbles: true }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                if (element.isContentEditable || element.getAttribute('contenteditable') === 'true' || element.getAttribute('role') === 'textbox') {
                    element.textContent = value;
                    element.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
                    element.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
                """,
                element,
                text,
            )
        )
    except Exception:
        return False


def type_into_active_element(driver: webdriver.Chrome, text: str) -> bool:
    try:
        element = driver.switch_to.active_element
    except Exception:
        return False
    if not element:
        return False
    return fill_editable_element(driver, element, text)


def cdp_click_viewport_point(driver: webdriver.Chrome, x: int, y: int) -> bool:
    try:
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        return True
    except Exception:
        return False


def try_fill_dialog_textarea_by_focus(driver: webdriver.Chrome, text: str) -> bool:
    for _ in range(6):
        if type_into_active_element(driver, text):
            return True
        try:
            driver.switch_to.active_element.send_keys(Keys.TAB)
        except Exception:
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.TAB)
            except Exception:
                return False
        time.sleep(0.3)
    return False


def try_fill_dialog_textarea_by_center_click(driver: webdriver.Chrome, text: str) -> bool:
    try:
        size = driver.get_window_size()
    except Exception:
        return False
    width = int(size.get("width", 0))
    height = int(size.get("height", 0))
    if width <= 0 or height <= 0:
        return False
    points = [
        (0.50, 0.24),
        (0.50, 0.28),
        (0.50, 0.32),
    ]
    for x_ratio, y_ratio in points:
        if not cdp_click_viewport_point(driver, int(width * x_ratio), int(height * y_ratio)):
            continue
        time.sleep(0.3)
        if try_fill_dialog_textarea_by_focus(driver, text):
            return True
    return False


def click_element(driver: webdriver.Chrome, element: WebElement) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(0.8)
    driver.execute_script("arguments[0].click();", element)


def top_card_root(driver: webdriver.Chrome) -> Optional[WebElement]:
    selectors = [
        (By.XPATH, "//main//*[contains(@class, 'pv-top-card')]"),
        (By.XPATH, "//main//section[.//*[contains(text(), 'Contact info')]]"),
        (By.XPATH, "//main//section[.//a[contains(@href, '/overlay/contact-info/')]]"),
    ]
    for by, selector in selectors:
        elements = driver.find_elements(by, selector)
        for element in elements:
            if element.is_displayed():
                return element
    return None


def top_card_has_text(driver: webdriver.Chrome, text: str) -> bool:
    root = top_card_root(driver)
    if not root:
        return False
    content = " ".join(
        filter(
            None,
            [
                root.text,
                root.get_attribute("innerText"),
            ],
        )
    ).lower()
    return text.lower() in content


def fill_textarea(driver: webdriver.Chrome, selectors: list[tuple[str, str]], text: str) -> bool:
    field = wait_for_any(driver, selectors)
    if not field:
        return False
    return fill_editable_element(driver, field, text)


def click_button_by_text(driver: webdriver.Chrome, texts: list[str]) -> bool:
    xpath_parts = [
        f"contains(normalize-space(), '{text}') or .//span[contains(normalize-space(), '{text}')]"
        for text in texts
    ]
    xpath = "//button[" + " or ".join(xpath_parts) + "]"
    button = wait_for_any(driver, [(By.XPATH, xpath)])
    if not button:
        return False
    click_element(driver, button)
    return True


def click_inside_dialog_by_labels(driver: webdriver.Chrome, labels: list[str]) -> bool:
    dialog_selectors = [
        (By.XPATH, "//*[@role='dialog']//button"),
        (By.XPATH, "//*[@role='dialog']//a"),
        (By.XPATH, "//*[@role='dialog']//*[@role='button']"),
        (By.XPATH, "//*[@data-test-modal]//button"),
        (By.XPATH, "//*[@data-test-modal]//a"),
        (By.XPATH, "//*[@data-test-modal]//*[@role='button']"),
    ]
    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        element = first_visible_matching(driver, dialog_selectors, labels)
        if element:
            click_element(driver, element)
            return True
        for element in deep_find_elements(driver, ["button", "a", "[role='button']"], labels):
            if element.is_enabled():
                click_element(driver, element)
                return True
        time.sleep(0.5)
    return False


def click_dialog_button_by_aria_label(driver: webdriver.Chrome, labels: list[str]) -> bool:
    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        for label in labels:
            selectors = [
                (By.XPATH, f"//button[@aria-label='{label}']"),
                (By.XPATH, f"//button[.//span[normalize-space()='{label}']]"),
                (By.XPATH, f"//*[@role='dialog']//button[@aria-label='{label}']"),
                (By.XPATH, f"//*[@data-test-modal]//button[@aria-label='{label}']"),
            ]
            for by, selector in selectors:
                for element in driver.find_elements(by, selector):
                    if element.is_enabled():
                        click_element(driver, element)
                        return True
        for element in deep_find_elements(driver, ["button", "a", "[role='button']"], labels):
            if element.is_enabled():
                click_element(driver, element)
                return True
        for node_id in cdp_find_nodes_by_attribute(driver, ["button"], "aria-label", labels):
            if cdp_click_node(driver, node_id):
                return True
        for backend_node_id in ax_find_backend_node_ids(driver, ["button"], labels):
            if cdp_click_backend_node(driver, backend_node_id):
                return True
        time.sleep(0.5)
    return False


def wait_for_dialog(driver: webdriver.Chrome) -> bool:
    selectors = [
        (By.XPATH, "//button[@aria-label='Add a note']"),
        (By.XPATH, "//button[@aria-label='Send without a note']"),
        (By.XPATH, "//button[@aria-label='Send invitation']"),
        (By.XPATH, "//*[@id='custom-message']"),
        (By.XPATH, "//*[@role='dialog']"),
        (By.XPATH, "//*[@data-test-modal]"),
    ]
    if wait_for_any(driver, selectors):
        return True
    if deep_wait_for_any(
        driver,
        ["button", "a", "[role='button']", "textarea", "dialog", "[role='dialog']", "[data-test-modal]"],
        ["Add a note", "Send without a note", "Send invitation"],
    ):
        return True
    if deep_wait_for_any(driver, ["#custom-message", "textarea"]):
        return True
    if cdp_find_nodes_by_attribute(
        driver,
        ["button"],
        "aria-label",
        ["Add a note", "Send without a note", "Send invitation"],
    ):
        return True
    if ax_find_backend_node_ids(driver, ["button"], ["Add a note", "Send without a note", "Send invitation"]):
        return True
    if cdp_find_nodes_by_attribute(driver, ["textarea"], "id", ["custom-message"]):
        return True
    return False


def wait_for_note_editor(driver: webdriver.Chrome) -> bool:
    selectors = [
        (By.XPATH, "//*[@id='custom-message']"),
        (By.XPATH, "(//*[@role='dialog'] | //*[@data-test-modal])//textarea"),
        (By.XPATH, "(//*[@role='dialog'] | //*[@data-test-modal])//*[@role='textbox']"),
        (By.XPATH, "(//*[@role='dialog'] | //*[@data-test-modal])//*[@contenteditable='true']"),
        (By.XPATH, "//*[contains(@placeholder, 'We know each other')]"),
        (By.XPATH, "//button[@aria-label='Send invitation']"),
        (By.XPATH, "//button[normalize-space()='Send']"),
        (By.XPATH, "//*[contains(normalize-space(), '0/200')]"),
    ]
    if wait_for_any(driver, selectors):
        return True
    if deep_wait_for_any(
        driver,
        [
            "#custom-message",
            "textarea",
            "[role='textbox']",
            "[contenteditable='true']",
            "[placeholder*='We know each other']",
            "button",
        ],
        ["We know each other", "Send", "0/200"],
    ):
        return True
    for node_id in cdp_find_editable_node_ids(driver, ["we know each other", "message", "200"]):
        resolved = driver.execute_cdp_cmd("DOM.resolveNode", {"nodeId": node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not object_id:
            continue
        visibility = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function() {
                        const style = this.ownerDocument.defaultView.getComputedStyle(this);
                        const rect = this.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && this.getAttribute('aria-hidden') !== 'true'
                            && rect.width > 0
                            && rect.height > 0;
                    }
                """,
                "returnByValue": True,
            },
        )
        if visibility.get("result", {}).get("value"):
            return True
    for backend_node_id in ax_find_editable_backend_node_ids(driver, ["we know each other", "message", "note", "200"]):
        try:
            resolved = driver.execute_cdp_cmd("DOM.resolveNode", {"backendNodeId": backend_node_id})
            object_id = resolved.get("object", {}).get("objectId")
            if not object_id:
                continue
            visibility = driver.execute_cdp_cmd(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": """
                        function() {
                            const style = this.ownerDocument.defaultView.getComputedStyle(this);
                            const rect = this.getBoundingClientRect();
                            return style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && this.getAttribute('aria-hidden') !== 'true'
                                && rect.width > 0
                                && rect.height > 0;
                        }
                    """,
                    "returnByValue": True,
                },
            )
            if visibility.get("result", {}).get("value"):
                return True
        except Exception:
            continue
    return False


def log_dialog_actions(driver: webdriver.Chrome) -> None:
    actions = driver.find_elements(
        By.XPATH,
        "(//*[@role='dialog'] | //*[@data-test-modal])//button | "
        "(//*[@role='dialog'] | //*[@data-test-modal])//a | "
        "(//*[@role='dialog'] | //*[@data-test-modal])//*[@role='button']",
    )
    labels: list[str] = []
    for action in actions:
        text = " ".join(
            filter(
                None,
                [
                    action.text.strip(),
                    action.get_attribute("aria-label"),
                ],
            )
        ).strip()
        if text:
            labels.append(text)
    for action in deep_find_elements(driver, ["button", "a", "[role='button']"]):
        text = " ".join(
            filter(
                None,
                [
                    action.text.strip(),
                    action.get_attribute("aria-label"),
                    action.get_attribute("innerText"),
                ],
            )
        ).strip()
        if text and text not in labels:
            labels.append(text)
    if labels:
        print(f"  Dialog actions seen: {labels}")


def log_visible_buttons(driver: webdriver.Chrome) -> None:
    buttons = driver.find_elements(By.XPATH, "//button | //a")
    labels: list[str] = []
    for button in buttons:
        if not button.is_displayed():
            continue
        text = " ".join(
            filter(
                None,
                [
                    button.text.strip(),
                    button.get_attribute("aria-label"),
                    button.get_attribute("href"),
                ],
            )
        ).strip()
        if text:
            labels.append(text)
    for button in deep_find_elements(driver, ["button", "a", "[role='button']"]):
        text = " ".join(
            filter(
                None,
                [
                    button.text.strip(),
                    button.get_attribute("aria-label"),
                    button.get_attribute("href"),
                    button.get_attribute("innerText"),
                ],
            )
        ).strip()
        if text and text not in labels:
            labels.append(text)
    if labels:
        print(f"  Visible actions on page: {labels[:40]}")


def click_connect_action(driver: webdriver.Chrome) -> bool:
    root = top_card_root(driver)
    if not root:
        return False

    selectors = [
        (By.XPATH, ".//a[contains(@aria-label, ' to connect')]"),
        (By.XPATH, ".//a[.//span[normalize-space()='Connect']]"),
        (By.XPATH, ".//button[contains(@aria-label, 'Connect')]"),
        (By.XPATH, ".//button[.//span[normalize-space()='Connect']]"),
    ]
    for by, selector in selectors:
        for element in root.find_elements(by, selector):
            if element.is_displayed() and element.is_enabled():
                click_element(driver, element)
                return True
    return False


def fill_dialog_textarea(driver: webdriver.Chrome, text: str) -> bool:
    end_time = time.time() + WAIT_SECONDS
    selectors = [
        (By.ID, "custom-message"),
        (By.XPATH, "//*[@id='custom-message']"),
        (By.XPATH, "(//*[@role='dialog'] | //*[@data-test-modal])//textarea"),
        (By.XPATH, "//textarea[@name='message']"),
        (By.XPATH, "(//*[@role='dialog'] | //*[@data-test-modal])//*[@role='textbox']"),
        (By.XPATH, "(//*[@role='dialog'] | //*[@data-test-modal])//*[@contenteditable='true']"),
        (By.XPATH, "//*[contains(@placeholder, 'We know each other')]"),
    ]
    while time.time() < end_time:
        for by, selector in selectors:
            for field in driver.find_elements(by, selector):
                if field.is_enabled() and fill_editable_element(driver, field, text):
                    return True
        for field in deep_find_elements(
            driver,
            [
                "#custom-message",
                "textarea[name='message']",
                "textarea",
                "[role='textbox']",
                "[contenteditable='true']",
                "[placeholder*='We know each other']",
            ],
        ):
            if field.is_enabled() and fill_editable_element(driver, field, text):
                return True
        for node_id in cdp_find_nodes_by_attribute(driver, ["textarea"], "id", ["custom-message"]):
            if cdp_set_node_value(driver, node_id, text):
                return True
        for node_id in cdp_find_nodes_by_attribute(driver, ["textarea"], "name", ["message"]):
            if cdp_set_node_value(driver, node_id, text):
                return True
        for node_id in reversed(
            cdp_find_editable_node_ids(
                driver,
                ["we know each other", "message", "custom-message", "200"],
            )
        ):
            if cdp_set_node_value(driver, node_id, text):
                return True
        for backend_node_id in reversed(
            ax_find_editable_backend_node_ids(
                driver,
                ["we know each other", "message", "note", "200"],
            )
        ):
            if cdp_set_backend_node_text(driver, backend_node_id, text):
                return True
        if type_into_active_element(driver, text):
            return True
        if try_fill_dialog_textarea_by_focus(driver, text):
            return True
        if try_fill_dialog_textarea_by_center_click(driver, text):
            return True
        time.sleep(0.5)
    return False


def visible_text_matches(element: WebElement, labels: list[str]) -> bool:
    content = " ".join(
        filter(
            None,
            [
                element.text.strip(),
                element.get_attribute("aria-label"),
                element.get_attribute("innerText"),
            ],
        )
    ).lower()
    return any(label.lower() in content for label in labels)


def first_visible_matching(driver: webdriver.Chrome, selectors: list[tuple[str, str]], labels: list[str]) -> Optional[WebElement]:
    for by, selector in selectors:
        for element in driver.find_elements(by, selector):
            if element.is_displayed() and element.is_enabled() and visible_text_matches(element, labels):
                return element
    return None


def click_profile_action(driver: webdriver.Chrome, labels: list[str]) -> bool:
    selectors = [
        (By.XPATH, "//main//button"),
        (By.XPATH, "//main//a"),
        (By.XPATH, "//main//*[@role='button']"),
        (By.XPATH, "//div[contains(@class, 'pv-top-card')]//button"),
        (By.XPATH, "//div[contains(@class, 'pv-top-card')]//a"),
        (By.XPATH, "//div[contains(@class, 'pv-top-card')]//*[@role='button']"),
    ]
    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        element = first_visible_matching(driver, selectors, labels)
        if element:
            click_element(driver, element)
            return True
        time.sleep(0.5)
    return False


def click_menu_action(driver: webdriver.Chrome, labels: list[str]) -> bool:
    selectors = [
        (By.XPATH, "//*[@role='menu']//button"),
        (By.XPATH, "//*[@role='menu']//*[@role='menuitem']"),
        (By.XPATH, "//div[contains(@class, 'artdeco-dropdown__content-inner')]//button"),
        (By.XPATH, "//div[contains(@class, 'artdeco-dropdown__content-inner')]//*[@role='button']"),
        (By.XPATH, "//div[contains(@class, 'artdeco-dropdown__content-inner')]//*[@role='menuitem']"),
    ]
    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        element = first_visible_matching(driver, selectors, labels)
        if element:
            click_element(driver, element)
            return True
        time.sleep(0.5)
    return False


def ensure_logged_in(driver: webdriver.Chrome) -> None:
    driver.get("https://www.linkedin.com/")
    time.sleep(3)
    if "feed" in driver.current_url or "linkedin.com/in/" in driver.current_url:
        return
    input("Log in to LinkedIn in the opened browser, then press Enter here to continue...")


def open_profile(driver: webdriver.Chrome, url: str) -> None:
    driver.get(url)
    WebDriverWait(driver, WAIT_SECONDS).until(
        lambda d: "linkedin.com/in/" in d.current_url or "linkedin.com/company/" in d.current_url
    )
    time.sleep(2)


def open_more_menu_if_needed(driver: webdriver.Chrome, desired_labels: list[str]) -> bool:
    if first_visible_matching(
        driver,
        [
            (By.XPATH, "//main//button"),
            (By.XPATH, "//main//a"),
            (By.XPATH, "//main//*[@role='button']"),
        ],
        desired_labels,
    ):
        return True

    more_button = wait_for_any(
        driver,
        [
            (By.XPATH, "//button[.//span[contains(normalize-space(), 'More')]]"),
            (By.XPATH, "//button[contains(@aria-label, 'More actions')]"),
            (By.XPATH, "//main//button[contains(@aria-label, 'More')]"),
        ],
    )
    if not more_button:
        print("  Could not find the More actions button")
        return False

    click_element(driver, more_button)
    time.sleep(1)
    return True


def send_connection_request(driver: webdriver.Chrome, task: ProfileTask) -> bool:
    print("  Looking for Connect action")
    if top_card_has_text(driver, "pending"):
        print("  Invitation is already pending for this profile")
        return True

    if not open_more_menu_if_needed(driver, ["Connect"]):
        return False

    if not click_connect_action(driver) and not click_menu_action(driver, ["Connect"]):
        print("  Could not find a visible Connect action")
        save_debug_artifacts(driver, "connect_not_found")
        return False

    time.sleep(1)
    dialog_detected = wait_for_dialog(driver)
    if not dialog_detected:
        print("  Connect dialog was not detected directly, attempting popup actions anyway")

    log_dialog_actions(driver)
    if task.note:
        print("  Connect dialog opened, trying Add a note")
        log_dialog_actions(driver)
        log_visible_buttons(driver)
        if not click_dialog_button_by_aria_label(driver, ["Add a note"]):
            log_dialog_actions(driver)
            log_visible_buttons(driver)
            print("  Could not find Add a note in the connect dialog")
            if not dialog_detected:
                save_debug_artifacts(driver, "connect_dialog_not_found")
            else:
                save_debug_artifacts(driver, "add_note_not_found")
            return False
        time.sleep(1)
        if not wait_for_note_editor(driver):
            print("  Add a note was clicked, but the note editor never became visible")
            save_debug_artifacts(driver, "add_note_editor_not_found")
            return False
        filled = fill_dialog_textarea(driver, task.note)
        if not filled:
            print("  Could not fill the connection note field")
            save_debug_artifacts(driver, "note_textarea_not_found")
            return False

    sent = click_dialog_button_by_aria_label(driver, ["Send invitation", "Send without a note"])
    if not sent:
        print("  Could not find the final Send button in the connect dialog")
        save_debug_artifacts(driver, "send_invitation_not_found")
    return sent


def send_message(driver: webdriver.Chrome, task: ProfileTask) -> bool:
    print("  Looking for Message action")
    if not open_more_menu_if_needed(driver, ["Message"]):
        return False

    if not click_profile_action(driver, ["Message"]) and not click_menu_action(driver, ["Message"]):
        print("  Could not find a visible Message action")
        return False

    filled = fill_textarea(
        driver,
        [
            (By.XPATH, "//div[@role='textbox' and @contenteditable='true']"),
            (By.XPATH, "//textarea"),
        ],
        task.message,
    )
    if not filled:
        print("  Could not find the message composer textbox")
        save_debug_artifacts(driver, "message_textbox_not_found")
        return False

    sent = (
        click_profile_action(driver, ["Send"])
        or click_menu_action(driver, ["Send"])
        or click_button_by_text(driver, ["Send"])
    )
    if not sent:
        print("  Could not find the Send button in the message dialog")
        save_debug_artifacts(driver, "message_send_not_found")
    return sent


def run_task(driver: webdriver.Chrome, task: ProfileTask) -> bool:
    open_profile(driver, task.url)

    if task.action == "c":
        return send_connection_request(driver, task)
    if task.action == "e":
        return send_message(driver, task)

    print(f"Unsupported action '{task.action}' for {task.url}")
    return False


def main() -> None:
    settings = load_settings(SETTINGS_FILE)
    tasks = load_tasks(INPUT_FILE)
    driver = build_driver(settings)
    results: list[tuple[str, str, bool]] = []

    try:
        ensure_logged_in(driver)
        for index, task in enumerate(tasks, start=1):
            print(f"[{index}/{len(tasks)}] Processing {task.action} -> {task.url}")
            try:
                success = run_task(driver, task)
            except Exception as exc:  # noqa: BLE001
                success = False
                print(f"  Failed: {type(exc).__name__}: {exc}")
                save_debug_artifacts(driver, "unexpected_error")
            results.append((task.url, task.action, success))
            time.sleep(2)
    finally:
        print("\nSummary")
        for url, action, success in results:
            status = "OK" if success else "FAILED"
            print(f"{status:7} {action} {url}")
        input("\nPress Enter to close the browser...")
        driver.quit()


if __name__ == "__main__":
    main()
