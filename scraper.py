import os
import io
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ── CONFIG ───────────────────────────────────────────────────────────────────
START_URL        = "scraping link... lol"
DRIVE_FOLDER     = "Images"   # Folder name to create in your Google Drive
MAX_PAGES        = 50
DELAY_SEC        = 1.5
IMG_FILTER       = "catalog/product"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.json"
# ─────────────────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── GOOGLE DRIVE AUTH ─────────────────────────────────────────────────────────

def get_drive_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"\n[!] '{CREDENTIALS_FILE}' not found.\n"
                    "    Download it from Google Cloud Console:\n"
                    "    APIs & Services > Credentials > OAuth 2.0 Client IDs > Download JSON\n"
                    f"    Then rename it to '{CREDENTIALS_FILE}' and place it next to this script."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def get_or_create_folder(service, folder_name):
    """Return the Drive folder ID, creating it if it doesn't exist."""
    query = (
        f"name='{folder_name}' "
        "and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        folder_id = files[0]["id"]
        print(f"[Drive] Using existing folder '{folder_name}' (id: {folder_id})")
        return folder_id

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    folder_id = folder["id"]
    print(f"[Drive] Created folder '{folder_name}' (id: {folder_id})")
    return folder_id


def file_exists_in_drive(service, folder_id, filename):
    query = (
        f"name='{filename}' "
        f"and '{folder_id}' in parents "
        "and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id)").execute()
    return len(results.get("files", [])) > 0


def upload_image_to_drive(service, folder_id, img_url):
    filename = os.path.basename(urlparse(img_url).path)

    if file_exists_in_drive(service, folder_id, filename):
        print(f"  [=] Already in Drive: {filename}")
        return

    try:
        resp = requests.get(img_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Could not download {img_url}: {e}")
        return

    ext = filename.rsplit(".", 1)[-1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

    file_metadata = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(io.BytesIO(resp.content), mimetype=mime, resumable=False)

    try:
        service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        print(f"  [+] Uploaded: {filename}")
    except Exception as e:
        print(f"  [!] Upload failed for {filename}: {e}")


# ── SCRAPING ──────────────────────────────────────────────────────────────────

def get_soup(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"  [!] Failed to fetch {url}: {e}")
        return None


def get_images(soup, page_url):
    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if not src:
            continue
        full_url = urljoin(page_url, src)
        if IMG_FILTER in full_url and full_url not in images:
            images.append(full_url)
    return images


def has_products(soup):
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if IMG_FILTER in src:
            return True
    return False


def bump_page(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    current = int(params.get("p", ["1"])[0])
    params["p"] = [str(current + 1)]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("Authenticating with Google Drive…")
    service = get_drive_service()
    folder_id = get_or_create_folder(service, DRIVE_FOLDER)
    print(f"Images will be uploaded to Drive folder: '{DRIVE_FOLDER}'\n")

    current_url = START_URL
    total_images = 0
    pages_visited = 0

    for page_num in range(1, MAX_PAGES + 1):
        print(f"[Page {page_num}] {current_url}")
        soup = get_soup(current_url)

        if not soup:
            print("  [!] Could not load page, stopping.")
            break

        if not has_products(soup):
            print("  [!] No product images found — reached end of catalog.")
            break

        images = get_images(soup, current_url)
        print(f"  Found {len(images)} product image(s)")

        for img_url in images:
            upload_image_to_drive(service, folder_id, img_url)
            time.sleep(0.2)

        total_images += len(images)
        pages_visited += 1

        print(f"  [~] Waiting {DELAY_SEC}s before page {page_num + 1}…\n")
        time.sleep(DELAY_SEC)
        current_url = bump_page(current_url)

    print(f"\n✓ Done!")
    print(f"  Pages visited  : {pages_visited}")
    print(f"  Images uploaded: {total_images}")
    print(f"  Drive folder   : '{DRIVE_FOLDER}'")


if __name__ == "__main__":
    main()