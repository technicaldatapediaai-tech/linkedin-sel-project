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
    click_element(driver, field)
    field.send_keys(Keys.CONTROL, "a")
    field.send_keys(Keys.DELETE)
    field.send_keys(text)
    return True


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
        time.sleep(0.5)
    return False


def wait_for_dialog(driver: webdriver.Chrome) -> bool:
    wait = WebDriverWait(driver, WAIT_SECONDS)
    selectors = [
        (By.XPATH, "//button[@aria-label='Add a note']"),
        (By.XPATH, "//button[@aria-label='Send without a note']"),
        (By.XPATH, "//button[@aria-label='Send invitation']"),
        (By.XPATH, "//*[@id='custom-message']"),
        (By.XPATH, "//*[@role='dialog']"),
        (By.XPATH, "//*[@data-test-modal]"),
    ]
    for by, selector in selectors:
        try:
            wait.until(EC.presence_of_element_located((by, selector)))
            return True
        except TimeoutException:
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
    ]
    while time.time() < end_time:
        for by, selector in selectors:
            for field in driver.find_elements(by, selector):
                if field.is_enabled():
                    click_element(driver, field)
                    field.send_keys(Keys.CONTROL, "a")
                    field.send_keys(Keys.DELETE)
                    field.send_keys(text)
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

    if not wait_for_dialog(driver):
        print("  Connect click did not open a dialog")
        save_debug_artifacts(driver, "connect_dialog_not_found")
        return False

    time.sleep(1)
    log_dialog_actions(driver)
    if task.note:
        print("  Connect dialog opened, trying Add a note")
        log_dialog_actions(driver)
        log_visible_buttons(driver)
        if not click_dialog_button_by_aria_label(driver, ["Add a note"]):
            log_dialog_actions(driver)
            log_visible_buttons(driver)
            print("  Could not find Add a note in the connect dialog")
            save_debug_artifacts(driver, "add_note_not_found")
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
