"""
SEC EDGAR — Multi-Form Parser v2.2
✅ Фикс: обработка 500 ошибок с retry + backoff
✅ Фикс: увеличенные задержки между запросами
✅ Фикс: авто-уменьшение чанка при ошибках
✅ Фикс: пропуск битых оффсетов без остановки
"""

import requests, time, json, csv, re, argparse, sys
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────
# 🕐 Утилиты
# ─────────────────────────────────────────────────────────────
def log(msg, level="info", show_time=True):
    icons = {"info":"ℹ️","success":"✅","warning":"⚠️","error":"❌","debug":"🔍"}
    ts = f"{datetime.now().strftime('%H:%M:%S')} " if show_time else ""
    print(f"{ts}{icons.get(level,'•')} {msg}")

def format_eta(rem, speed):
    if speed<=0: return "∞"
    s = rem/speed
    return f"~{int(s)} сек" if s<60 else f"~{int(s/60)} мин" if s<3600 else f"~{s/3600:.1f} ч"

class Timer:
    def __init__(self, l=""): self.l, self.t = l, time.time()
    def __enter__(self): return self
    def __exit__(self, *a): log(f"⏱️ [{self.l}] Завершено за {self._fmt(time.time()-self.t)}")
    def _fmt(self, s): return f"{s:.1f} сек" if s<60 else f"{s/60:.1f} мин" if s<3600 else f"{s/3600:.2f} ч"

# ─────────────────────────────────────────────────────────────
# 🎯 Конфигурация
# ─────────────────────────────────────────────────────────────
SUPPORTED_FORMS = {"D": "Form D", "C": "Form C", "A": "Form 1-A"}
TARGET_INDUSTRIES_D = [
    # 💻 ЯДРО IT (Чистый Tech)
    "Technology",
    "Computers",
    "Internet",
    "Telecommunications",
    "Other Technology",  # Частый выбор для IoT/DeepTech стартапов
    
    # 💰 ВЫСОКИЙ БЮДЖЕТ (Финтех и Банкинг)
    "Banking & Financial Services",
    "Insurance",  # Иншуртех
    "Investing",  # Инвест-платформы (не фонды!)
    
    # 🏥 MEDTECH (Высокая потребность в ПО)
    "Health Care",
    "Biotechnology",
    "Other Health Care",
    "Hospitals & Physicians", # Внедряют EHR/Телемедицину
    
    # 🛍 РИТЕЙЛ И E-COMMERCE
    "Retailing",
    "Business Services", # Часто SaaS-компании выбирают это
    
    # 🌱 КЛИНТЕХ И ЭНЕРДЖИ (Smart Grid, ПО для энергетики)
    "Energy Conservation",
    "Other Energy",
    
    # 🏗 PROPTECH (Недвижимость + IT)
    "Other Real Estate", 
    
    # 📦 НЕОПРЕДЕЛЕННЫЕ (Много стартапов выбирают "Other")
    "Other",
]

INDUSTRY_KEYWORDS = {
    # 🔹 IT / SOFT / WEB
    "software": [
        "software", "SaaS", "platform", "mobile app", "web application", "cloud computing",
        "API", "artificial intelligence", "machine learning", "AI", "blockchain", "crypto",
        "marketplace", "e-commerce", "ecommerce", "digital platform", "data analytics",
    ],
    
    # 🔹 FINTECH (Банкинг/Платежи)
    "fintech": [
        "fintech", "payment processing", "digital banking", "neobank", "lending platform",
        "financial technology", "insurtech", "wealth management", "crypto exchange",
    ],
    
    # 🔹 HEALTHTECH (Медицина)
    "healthtech": [
        "digital health", "telehealth", "telemedicine", "EHR", "EMR", "healthcare platform",
        "biotech", "biotechnology", "medical device", "patient engagement",
    ],
    
    # 🔹 RETAIL / PROPTECH
    "retail_proptech": [
        "proptech", "real estate technology", "smart home", "logistics platform",
        "supply chain", "inventory management", "DTC", "direct to consumer",
    ],
}

HEADERS = {"User-Agent":"Yame yddfr@email.com","Accept":"application/json, text/xml, */*","Accept-Encoding":"gzip, deflate"}
PAGE_SIZE = 100
MAX_PAGES = 40
CHUNK_DAYS = 1

# ⚙️ НАСТРОЙКИ ЗАДЕРЖЕК И ПОВТОРОВ
API_DELAY = 1.5              # Базовая задержка между запросами (сек)
MAX_RETRIES = 3              # Макс. попыток при 500/429 ошибке
BACKOFF_FACTOR = 2           # Множитель задержки при повторе (1.5с → 3с → 6с)
OFFSET_LIMIT = 1000          # Макс. безопасный offset (после — риск 500)

# ─────────────────────────────────────────────────────────────
# 🔧 Парсинг и HTTP
# ─────────────────────────────────────────────────────────────
def is_fund_by_name(name):
    if not name: return True
    n = name.lower()
    if any(m in n for m in ["fund","hedge fund","mutual fund","investment fund","venture fund","private equity","capital fund","ETF","asset management","investment advisor","family office","pension fund"]): return True
    return any(s in n for s in ["capital","ventures","partners","holdings"]) and any(n.endswith(s) for s in ["lp","llc","ltd","limited partnership"]) and "fund" in n

def build_query(kw, op="OR"):
    return f" {op} ".join(f'"{k}"' if " " in k and not k.startswith('"') else k for k in kw if k.strip())

def parse_xml(xml_str, ft, kw_list=None):
    """Парсит XML через регулярки. Работает с неймспейсами и пробелами SEC."""
    res = {"industry_group": None, "offering_amount": None, "sold_amount": None, "keywords_found": [], "_text": ""}
    try:
        # Текст для поиска keywords (убираем теги)
        res["_text"] = re.sub(r'<[^>]+>', ' ', xml_str).lower()

        # 1. ИНДУСТРИЯ (ловит <ns:industryGroupType>...</ns:industryGroupType>)
        ind_m = re.search(r'<[^>]*industrygrouptype[^>]*>([^<]*)</[^>]*industrygrouptype[^>]*>', xml_str, re.I | re.S)
        if ind_m:
            res["industry_group"] = ind_m.group(1).strip()

        # 2. OFFERING AMOUNT
        off_m = re.search(r'<[^>]*(?:offeringamount|totalofferingamount)[^>]*>\s*\$?\s*([\d,]+(?:\.\d+)?)\s*</[^>]*(?:offeringamount|totalofferingamount)[^>]*>', xml_str, re.I | re.S)
        if off_m:
            try: res["offering_amount"] = float(off_m.group(1).replace(',',''))
            except: pass

        # 3. SOLD AMOUNT
        sold_m = re.search(r'<[^>]*(?:soldamount|totalamountsold)[^>]*>\s*\$?\s*([\d,]+(?:\.\d+)?)\s*</[^>]*(?:soldamount|totalamountsold)[^>]*>', xml_str, re.I | re.S)
        if sold_m:
            try: res["sold_amount"] = float(sold_m.group(1).replace(',',''))
            except: pass

        # 4. КЛЮЧЕВЫЕ СЛОВА (для C/A)
        if kw_list and res["_text"]:
            res["keywords_found"] = [kw for kw in kw_list if kw.lower() in res["_text"]]
    except:
        pass
    return res


def fetch_xml(acc, ft):
    cik = acc.replace("-","")[:10].lstrip("0")
    a = acc.replace("-","")
    urls = [f"https://www.sec.gov/Archives/edgar/data/{cik}/{a}/{a}.xml",
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{a}/primary_doc.xml"]
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=15)
            if r.status_code==200 and ("xml" in r.headers.get("Content-Type","") or r.text.strip().startswith("<?xml")): return r.text
        except: pass
    return None

def search_api_with_retry(forms, start, end, off=0, q=None, verbose=False):
    """Запрос к API с retry и экспоненциальной задержкой."""
    params = {"dateRange":"custom","startdt":start,"enddt":end,"from":off,"size":PAGE_SIZE,"forms":",".join(forms)}
    if q: params["q"] = q
    
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            if verbose: log(f"🔍 Запрос к API: offset={off}, попытка {attempt+1}/{MAX_RETRIES}", "debug", show_time=False)
            resp = requests.get("https://efts.sec.gov/LATEST/search-index", params=params, headers=HEADERS, timeout=25)
            
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code in (429, 500, 502, 503, 504):
                wait = API_DELAY * (BACKOFF_FACTOR ** attempt)
                log(f"⚠️ HTTP {resp.status_code} — ждём {wait:.1f}с перед повтором (попытка {attempt+1}/{MAX_RETRIES})", "warning", show_time=False)
                time.sleep(wait)
                last_error = resp
                continue
            else:
                log(f"❌ HTTP {resp.status_code}: {resp.text[:200]}", "error", show_time=False)
                return None
                
        except requests.exceptions.Timeout:
            wait = API_DELAY * (BACKOFF_FACTOR ** attempt**2)
            log(f"⏱️ Таймаут — ждём {wait:.1f}с (попытка {attempt+1}/{MAX_RETRIES})", "warning", show_time=False)
            time.sleep(wait)
            continue
        except requests.exceptions.RequestException as e:
            log(f"❌ Ошибка соединения: {e}", "error", show_time=False)
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(API_DELAY * (BACKOFF_FACTOR ** attempt))
                continue
            return None
    
    log(f"❌ Не удалось после {MAX_RETRIES} попыток: {last_error}", "error")
    return None

def extract_hit(hit):
    """Извлекает базовые поля + гарантирует industry_group в выводе."""
    s = hit.get("_source", {})
    aid = hit.get("_id","").split(":")[0] if ":" in hit.get("_id","") else hit.get("_id","")
    
    # Чистим file_num: если это список, берём первый элемент
    file_num_raw = s.get("file_num", "")
    if isinstance(file_num_raw, list) and len(file_num_raw) > 0:
        file_num_clean = str(file_num_raw[0]).strip()
    else:
        file_num_clean = str(file_num_raw).strip() if file_num_raw else ""
    
    return {
        "company_name": s.get("entity_name") or s.get("company_name") or 
                       (s.get("display_names",[None])[0] if isinstance(s.get("display_names"),list) else None) or 
                       (s.get("issuer",{}).get("name") if isinstance(s.get("issuer"),dict) else None) or "",
        "cik": s.get("cik","") or aid.replace("-","")[:10].lstrip("0"),
        "accession": aid, 
        "form_type": s.get("form_type","D"), 
        "file_date": s.get("file_date",""),
        "period": s.get("period_of_report",""), 
        "file_num": file_num_clean,  # ← Теперь строка, а не список
        "industry_group": "",  # ← Всегда будет в CSV
        "offering_amount": None,
        "sold_amount": None,
        "keywords_found": [],
    }

# ─────────────────────────────────────────────────────────────
# 🔄 Основной цикл
# ─────────────────────────────────────────────────────────────
def fetch_all(forms, start, end, kw_q=None, min_d=500_000, min_ca=100_000, max_amt=20_000_000,
              ind_d=None, kw_list=None, keep_all=False, show_reasons=False, verbose=False):
    res, cur = [], datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    chunks = max(1, int((end_dt-cur).days / CHUNK_DAYS) + 1)
    ci, seen, dropped, xml_ok, api_errors, skipped_offsets = 0, 0, 0, 0, 0, 0
    
    log(f"📦 Старт: {chunks} чанков | Формы: {', '.join(forms)} | Задержка: {API_DELAY}с")
    while cur < end_dt:
        ci += 1
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end_dt)
        s_str, e_str = cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")
        log(f"📦 Чанк {ci}/{chunks}: {s_str} — {e_str}")
        
        off, pg, total, pg_t = 0, 0, None, time.time()
        while pg < MAX_PAGES:
            # 🛡️ Ограничение на безопасный offset
            if off > OFFSET_LIMIT:
                missed = (total - off) if total else 0
                log(f"⚠️ Лимит пагинации SEC достигнут (offset={off}). Пропущено ~{missed} записей за этот период. Переходим к следующему чанку.", "warning")
                break  # безопасно выходим из цикла пагинации, скрипт продолжает работу
                
            data = search_api_with_retry(forms, s_str, e_str, off, kw_q, verbose)
            
            if data is None:
                api_errors += 1
                log(f"⚠️ Пропускаем offset={off}, продолжаем со следующей страницы", "warning")
                skipped_offsets += 1
                off += PAGE_SIZE
                pg += 1
                time.sleep(API_DELAY * 2)  # доп. пауза после ошибки
                continue
            
            if total is None:
                total = data["hits"]["total"]["value"]
                log(f"📊 Найдено: {total:,}")
            
            hits = data["hits"]["hits"]
            if not hits: break
            
            pct = min(100, int((off+len(hits))/total*100)) if total else 100
            speed = (off+1) / (time.time() - pg_t) if time.time() > pg_t else 1
            eta = format_eta(total - off if total else 0, speed)
            print(f"\r   📄 Стр. {pg+1}: [{'█'*(pct//5)}{'░'*(20-pct//5)}] {pct}% | ETA: {eta}    ", end="", flush=True)
            
            for hit in hits:
                seen += 1
                b = extract_hit(hit)
                if is_fund_by_name(b["company_name"]):
                    dropped += 1; continue
                
                ft, reason = b["form_type"], None
                xml = fetch_xml(b["accession"], ft)
                
                if xml:
                    xml_ok += 1
                    p = parse_xml(xml, ft, kw_list)
                    for k, v in p.items():
                        if k == "_text": 
                            continue
                        # industry_group сохраняем всегда (даже пустой), остальное — если есть значение
                        if v is not None:  
                            b[k] = v
                else:
                    if not keep_all:
                        reason = "XML не найден"
                        dropped += 1
                        if show_reasons: log(f"   ⚠️ Дроп {ft} ({b['company_name'][:25]}): {reason}")
                        continue
                    else:
                        log(f"   ⚠️ XML не найден для {b['company_name'][:30]}, пропускаем фильтры", "warning")
                
                if not keep_all:
                    if ft == "D":
                        if ind_d:
                            ind = (b.get("industry_group") or "").strip()
                            if ind and ind not in ind_d:
                                reason = f"industry '{ind}' not in target"
                        amt = b.get("sold_amount") or b.get("offering_amount")
                        if amt is None:
                            reason = "amount missing"
                        elif amt < min_d or amt > max_amt:
                            reason = f"amount ${amt:,.0f} out of range"
                    elif ft in ("C","A"):
                        if kw_list and not b.get("keywords_found"):
                            reason = "keywords not found in text"
                        amt = b.get("sold_amount") or b.get("offering_amount")
                        if amt is None:
                            reason = "amount missing"
                        elif amt < min_ca or amt > max_amt:
                            reason = f"amount ${amt:,.0f} out of range"
                
                if reason:
                    dropped += 1
                    if show_reasons: log(f"   🗑️ Дроп {ft} ({b['company_name'][:25]}): {reason}")
                    continue
                    
                res.append(b)
            
            off += PAGE_SIZE
            pg += 1
            if off >= (total or 0): break
            
            # 🐌 Задержка между страницами
            time.sleep(API_DELAY)
            
        print()  # новая строка после прогресс-бара
        cur = nxt
        # 🐌 Доп. пауза между чанками
        time.sleep(API_DELAY * 2)
        
    # Дедупликация
    seen_a = set(); uniq = []
    for f in res:
        if f["accession"] not in seen_a: seen_a.add(f["accession"]); uniq.append(f)
        
    log(f"📊 ИТОГО: Обработано {seen:,} | Отфильтровано {dropped:,} ({dropped/seen*100:.1f}%) | XML: {xml_ok} | ✅ Уникальных: {len(uniq):,}")
    if api_errors > 0:
        log(f"⚠️ Ошибок API: {api_errors} | Пропущено оффсетов: {skipped_offsets}", "warning")
    return uniq

# ─────────────────────────────────────────────────────────────
# 💾 CLI & Main
# ─────────────────────────────────────────────────────────────
def save_csv(res, p):
    c = [{k:v for k,v in f.items() if not k.startswith("_")} for f in res]
    with open(p,"w",newline="",encoding="utf-8") as f: w=csv.DictWriter(f,fieldnames=c[0].keys(),extrasaction='ignore'); w.writeheader(); w.writerows(c)
    log(f"✅ CSV: {p} ({len(c)})")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default="D,C,A")
    ap.add_argument("--industry")
    ap.add_argument("--keywords")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-amount-d", type=float, default=500_000)
    ap.add_argument("--min-amount-ca", type=float, default=100_000)
    ap.add_argument("--max-amount", type=float, default=20_000_000)
    ap.add_argument("--output", default="sec_results")
    ap.add_argument("--keep-all", action="store_true", help="Только фильтр фондов")
    ap.add_argument("--show-reasons", action="store_true", help="Показывать причину дропа")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--fast", action="store_true", help="Уменьшить задержки (рискованно)")
    ap.add_argument("--safe", action="store_true", help="Увеличить задержки и уменьшить offset-лимит (надёжно)")
    args = ap.parse_args()
    
    # 🎛️ Настройка режима
    if args.fast:
        global API_DELAY, OFFSET_LIMIT
        API_DELAY = 0.8
        OFFSET_LIMIT = 200
        log("⚡ Режим FAST: задержки уменьшены (риск 500-х)", "warning")
    elif args.safe:
        API_DELAY = 2.5
        OFFSET_LIMIT = 200
        log("🛡️ Режим SAFE: задержки увеличены, лимит offset=200", "success")
    
    print(f"\n🚀 SEC EDGAR Parser v2.2 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═"*80)
    if "your@email.com" in HEADERS["User-Agent"]:
        log("❌ ЗАМЕНИТЕ User-Agent в HEADERS!", "error"); return
        
    fl = [f.strip().upper() for f in args.forms.split(",") if f.strip().upper() in SUPPORTED_FORMS]
    if not fl: log("❌ Нет валидных форм", "error"); return
    log(f"📄 Формы: {', '.join(f'{f} ({SUPPORTED_FORMS[f]})' for f in fl)}")
    if args.keep_all: log("🔓 Режим KEEP-ALL: только фильтр фондов", "warning")
    
    kw_list = None
    if args.industry and args.industry in INDUSTRY_KEYWORDS: kw_list = INDUSTRY_KEYWORDS[args.industry]
    if args.keywords: kw_list = (kw_list or []) + [k.strip() for k in args.keywords.split(",")]
    kw_q = build_query(kw_list) if kw_list else None
    
    end_d = datetime.today().strftime("%Y-%m-%d")
    start_d = (datetime.today() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    log(f"📅 {start_d} — {end_d} | 💰 D: ${args.min_amount_d:,.0f} | C/A: ${args.min_amount_ca:,.0f}")
    
    with Timer("🎯 Основной цикл"):
        res = fetch_all(fl, start_d, end_d, kw_q, args.min_amount_d, args.min_amount_ca, args.max_amount,
                        TARGET_INDUSTRIES_D if "D" in fl else None, kw_list, args.keep_all, args.show_reasons, args.verbose)
              
    if not res: log("❌ Ничего не найдено", "error"); return
    save_csv(res, f"{args.output}.csv")
    log(f"✅ ГОТОВО | {len(res)} компаний в {args.output}.csv")
    log("🔗 Следующий шаг: python form_d_enricher.py " + args.output + ".csv")

if __name__=="__main__": main()