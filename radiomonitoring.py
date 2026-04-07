#!/usr/bin/env python3
"""
Radiomonitoring task solver.
Interacts with hub.ag3nts.org/verify to:
1. Start a session
2. Listen for radio intercepts (text or binary)
3. Analyze data to find the city called "Syjon" (Zion)
4. Transmit final report
"""

import base64
import json
import os
import re
import sys
import time
import requests

API_URL = "https://hub.ag3nts.org/verify"
API_KEY = os.environ.get("RADIOMONITORING_API_KEY", "8ef4542b-b7a0-4420-9da0-1ee8f8ef4f16")
TASK = "radiomonitoring"

def post(answer: dict) -> dict:
    payload = {"apikey": API_KEY, "task": TASK, "answer": answer}
    r = requests.post(API_URL, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def start_session():
    print("[*] Starting session...")
    resp = post({"action": "start"})
    print(f"    -> {resp}")
    return resp


def listen():
    resp = post({"action": "listen"})
    return resp


def decode_attachment(b64_data: str) -> bytes:
    return base64.b64decode(b64_data)


def process_attachment(data: bytes, meta: str) -> str:
    """Try to extract useful text from binary attachments."""
    # Try to decode as UTF-8 text first
    try:
        text = data.decode("utf-8")
        return f"[attachment text]\n{text}"
    except UnicodeDecodeError:
        pass

    # Try latin-1 as fallback
    try:
        text = data.decode("latin-1")
        # Check if it looks like printable text (> 70% printable chars)
        printable = sum(1 for c in text if c.isprintable() or c in '\n\r\t')
        if printable / len(text) > 0.7:
            return f"[attachment text (latin-1)]\n{text}"
    except Exception:
        pass

    # For JSON meta, try parsing
    if "json" in meta.lower():
        try:
            obj = json.loads(data)
            return f"[attachment json]\n{json.dumps(obj, ensure_ascii=False, indent=2)}"
        except Exception:
            pass

    return f"[binary attachment, size={len(data)} bytes, meta={meta}]"


def collect_intercepts(max_rounds: int = 60) -> list[str]:
    """Listen until done or max_rounds reached. Returns list of text fragments."""
    fragments = []
    for i in range(max_rounds):
        print(f"[*] Listen round {i+1}...")
        try:
            resp = listen()
        except Exception as e:
            print(f"    Error: {e}")
            time.sleep(2)
            continue

        code = resp.get("code", 0)
        message = resp.get("message", "")
        print(f"    code={code} message={message[:80]}")

        # Check if we're done
        done_phrases = ["enough data", "wystarczająco", "no more", "finished", "complete",
                        "end of", "koniec", "done", "zakończono"]
        if any(p in message.lower() for p in done_phrases):
            print("[*] Server indicates we have enough data.")
            fragments.append(f"[system] {message}")
            break

        # Error / rate-limit codes
        if code == 429 or code == 503:
            print("    Rate limited, waiting 5s...")
            time.sleep(5)
            continue

        # Text transcription
        if "transcription" in resp:
            text = resp["transcription"]
            print(f"    transcription: {text[:120]}")
            fragments.append(text)

        # Binary attachment
        elif "attachment" in resp:
            meta = resp.get("meta", "application/octet-stream")
            b64 = resp["attachment"]
            filesize = resp.get("filesize", 0)
            print(f"    attachment meta={meta} filesize={filesize}")
            raw = decode_attachment(b64)
            extracted = process_attachment(raw, meta)
            print(f"    extracted: {extracted[:120]}")
            fragments.append(extracted)

        else:
            # Likely noise / no useful content
            print(f"    (no transcription or attachment - skipping)")

        # Small delay to be polite
        time.sleep(0.5)

    return fragments


def analyze_with_openai(fragments: list[str]) -> dict:
    """Use OpenAI to extract city info from collected fragments."""
    from openai import OpenAI

    client = OpenAI()  # uses OPENAI_API_KEY env var

    combined = "\n---\n".join(fragments)
    # Truncate if too long (~100k chars max)
    if len(combined) > 100_000:
        print(f"[!] Combined text too long ({len(combined)} chars), truncating to 100k")
        combined = combined[:100_000]

    prompt = f"""You are an intelligence analyst. Below are intercepted radio communications. 
Your task is to extract specific information about a city codenamed "Syjon" (Zion in Polish).

From the communications, determine:
1. cityName - the real name of the city called "Syjon"
2. cityArea - the area of the city rounded to exactly 2 decimal places (e.g. "12.34")
3. warehousesCount - the number of warehouses in Syjon (integer)
4. phoneNumber - the phone number of the contact person from Syjon

Respond ONLY with a JSON object like:
{{
  "cityName": "NazwaMiasta",
  "cityArea": "12.34",
  "warehousesCount": 321,
  "phoneNumber": "123456789"
}}

Intercepted materials:
{combined}
"""

    print("[*] Sending to OpenAI for analysis...")
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    print(f"[*] OpenAI response: {content}")
    return json.loads(content)


def transmit_report(city_name: str, city_area: str, warehouses_count: int, phone_number: str):
    print(f"[*] Transmitting final report...")
    resp = post({
        "action": "transmit",
        "cityName": city_name,
        "cityArea": city_area,
        "warehousesCount": warehouses_count,
        "phoneNumber": phone_number,
    })
    print(f"[*] Final response: {resp}")
    return resp


def main():
    # Step 1: Start session
    start_session()
    time.sleep(1)

    # Step 2: Collect intercepts
    fragments = collect_intercepts(max_rounds=80)
    print(f"\n[*] Collected {len(fragments)} fragments")

    if not fragments:
        print("[!] No fragments collected, aborting.")
        sys.exit(1)

    # Save raw fragments for debugging
    with open("/tmp/radiomonitoring_fragments.json", "w", encoding="utf-8") as f:
        json.dump(fragments, f, ensure_ascii=False, indent=2)
    print("[*] Fragments saved to /tmp/radiomonitoring_fragments.json")

    # Step 3: Analyze
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        print("[!] OPENAI_API_KEY not set. Printing fragments and exiting.")
        for i, frag in enumerate(fragments):
            print(f"\n--- Fragment {i+1} ---\n{frag}")
        sys.exit(1)

    result = analyze_with_openai(fragments)

    city_name = result.get("cityName", "")
    city_area = result.get("cityArea", "")
    warehouses_count = result.get("warehousesCount", 0)
    phone_number = result.get("phoneNumber", "")

    if not all([city_name, city_area, warehouses_count, phone_number]):
        print(f"[!] Incomplete result from analysis: {result}")
        sys.exit(1)

    # Ensure cityArea has exactly 2 decimal places
    try:
        area_float = float(city_area)
        city_area = f"{area_float:.2f}"
    except ValueError:
        print(f"[!] Could not parse cityArea: {city_area}")
        sys.exit(1)

    # Step 4: Transmit
    final = transmit_report(city_name, city_area, int(warehouses_count), str(phone_number))
    return final


if __name__ == "__main__":
    main()
