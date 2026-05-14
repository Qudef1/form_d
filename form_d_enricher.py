import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI


def load_api_client():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found. Set it in your environment or .env file.")
    return OpenAI(api_key=api_key)


def build_prompt(company_name: str, cik: Optional[str]) -> str:
    prompt = f"""
You are an accuracy-focused data enrichment assistant. Use the web-search capability to find the public business contact details for the target company.

Target:
- Company name: {company_name}
"""
    if cik:
        prompt += f"- CIK: {cik}\n"
    prompt += """
Your task:
1. Find the official company LinkedIn page.
2. Find a publicly available company contact email address (company/generic email, not a private person email if possible).
3. Find the official company website URL.

Required output:
Return ONLY valid JSON with the following fields:
{{
  "linkedin": "LinkedIn company URL or null",
  "email": "public contact email or null",
  "website": "official website URL or null"
}}

If a value cannot be reliably found, return null for that field. Do not add extra text outside the JSON object.
"""
    return prompt.strip()


def extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def extract_fallbacks(text: str) -> dict:
    result = {"linkedin": None, "email": None, "website": None}
    linkedin_match = re.search(r'https?://(?:www\.)?linkedin\.com/[A-Za-z0-9_/\-\?=&%]+', text)
    if linkedin_match:
        result["linkedin"] = linkedin_match.group(0).rstrip('.,;')

    email_match = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)
    if email_match:
        result["email"] = email_match.group(0).rstrip('.,;')

    website_match = re.search(r'https?://(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,6}(?:/[A-Za-z0-9_\-./?=&%]*)?', text)
    if website_match:
        url = website_match.group(0).rstrip('.,;')
        if "linkedin.com" not in url:
            result["website"] = url
    return result


def enrich_row(client: OpenAI, company_name: str, cik: Optional[str] = None, verbose: bool = False) -> dict:
    prompt = build_prompt(company_name, cik)
    if verbose:
        print(f"\nEnriching: {company_name} | CIK={cik}")

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini-search-preview",
            max_tokens=400,
            web_search_options={"user_location": {"type": "approximate", "approximate": {"country": "US"}}},
            messages=[
                {"role": "system", "content": "You must respond with valid JSON only, no additional text."},
                {"role": "user", "content": prompt}
            ]
        )
        content = completion.choices[0].message.content
    except Exception as exc:
        return {
            "linkedin": None,
            "email": None,
            "website": None
        }
    
    data = extract_json(content)
    if not data:
        fallback = extract_fallbacks(content)
        data = {
            "linkedin": fallback.get("linkedin"),
            "email": fallback.get("email"),
            "website": fallback.get("website")
        }
    else:
        for field in ("linkedin", "email", "website"):
            if field in data and data[field] is not None:
                data[field] = str(data[field]).strip() or None
            else:
                data[field] = None

    return data


def main():
    parser = argparse.ArgumentParser(description="Enrich Form D results with LinkedIn, website and contact email using GPT web search.")
    parser.add_argument("--input", required=True, help="Input CSV file produced by form_d_companies.py")
    parser.add_argument("--output", default=None, help="Output CSV file path")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of rows to enrich")
    parser.add_argument("--verbose", action="store_true", help="Print progress details")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.exists(input_path):
        print(f"Input file not found: {input_path}")
        sys.exit(1)

    output_path = args.output or f"enriched_{Path(input_path).stem}.csv"
    client = load_api_client()

    df = pd.read_csv(input_path, low_memory=False)
    if args.limit:
        df = df.head(args.limit)

    if "linkedin" not in df.columns:
        df["linkedin"] = None
    if "website" not in df.columns:
        df["website"] = None
    if "email" not in df.columns:
        df["email"] = None

    for index, row in df.iterrows():
        company_name = str(row.get("company_name") or row.get("Company") or "").strip()
        cik = str(row.get("cik") or row.get("CIK") or "").strip() or None
        if not company_name:
            continue

        enriched = enrich_row(client, company_name, cik, verbose=args.verbose)
        df.at[index, "linkedin"] = enriched.get("linkedin")
        df.at[index, "email"] = enriched.get("email")
        df.at[index, "website"] = enriched.get("website")

        if args.verbose:
            print(f"  -> linkedin={enriched.get('linkedin')} email={enriched.get('email')} website={enriched.get('website')}")

    df.to_csv(output_path, index=False)
    print(f"Saved enriched results to {output_path}")


if __name__ == "__main__":
    main()
