# LinkedIn Action Runner

This project opens LinkedIn profile URLs from an input array and performs an explicit action per entry:

- `c`: send a connect request with a note
- `e`: send a LinkedIn message

## Important

Use this only in ways that comply with LinkedIn's terms and your account permissions. This project does not include bot evasion, stealth browsing, CAPTCHA bypassing, or fingerprint spoofing.

## Setup

1. Install Python 3.10+.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Update [`profiles.json`](./profiles.json) with your target profiles and message text.

## Where to add LinkedIn profile links

Put your LinkedIn profile URLs in [`profiles.json`](./profiles.json).

Example:

```json
[
  {
    "url": "https://www.linkedin.com/in/person-one/",
    "action": "c",
    "note": "Hi, I would like to connect with you."
  },
  {
    "url": "https://www.linkedin.com/in/person-two/",
    "action": "e",
    "message": "Hello, I wanted to reach out to you."
  }
]
```

## Use Your Existing Chrome Session

Selenium cannot control an already-open normal Chrome window unless that Chrome session was started with a remote debugging port.

1. Close Chrome windows you want Selenium to control.
2. Start Chrome manually from PowerShell:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

3. Confirm [`settings.json`](./settings.json) contains:

```json
{
  "attach_to_existing_browser": true,
  "debugger_address": "127.0.0.1:9222"
}
```

4. Log in to LinkedIn in that Chrome window.
5. Run:

```powershell
python linkedin_automation.py
```

If you leave `attach_to_existing_browser` as `false`, the script opens its own Chrome session using the local `chrome-profile` folder.

## Input format

`profiles.json`

```json
[
  {
    "url": "https://www.linkedin.com/in/example-person/",
    "action": "c",
    "note": "Hi, I would like to connect with you."
  },
  {
    "url": "https://www.linkedin.com/in/example-person-2/",
    "action": "e",
    "message": "Hello, I wanted to reach out regarding a business opportunity."
  }
]
```

## Run

```powershell
python linkedin_automation.py
```

On first run, Chrome will open LinkedIn. Log in manually, then return to the terminal and press Enter. The browser profile is reused from the local `chrome-profile` folder so you do not need to log in every time.

## Notes

- If a profile does not support the requested action, the script logs the failure and continues.
- Selectors on LinkedIn can change over time. If they do, update the button locators in [`linkedin_automation.py`](./linkedin_automation.py).
