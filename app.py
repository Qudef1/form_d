"""SEC EDGAR Parser — Streamlit UI"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from form_d_companies import (
    fetch_all, TARGET_INDUSTRIES_D, INDUSTRY_KEYWORDS,
    SUPPORTED_FORMS, build_query
)
from form_d_enricher import load_api_client, enrich_row

st.set_page_config(page_title="SEC EDGAR Parser", layout="wide")

st.title("SEC EDGAR Parser")
st.markdown("Парсер Form D/C/A с фильтрацией по индустриям и ключевым словам")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Настройки парсинга")
    forms = st.multiselect(
        "Формы",
        options=list(SUPPORTED_FORMS.keys()),
        default=["D"],
        format_func=lambda x: f"{x} ({SUPPORTED_FORMS[x]})"
    )
    days = st.number_input("Дней назад", min_value=1, max_value=365, value=30)
    
    st.subheader("Фильтры")
    selected_industries = st.multiselect(
        "Индустрии (Form D)",
        options=TARGET_INDUSTRIES_D,
        default=TARGET_INDUSTRIES_D[:5]
    )
    
    selected_keywords_cat = st.multiselect(
        "Категории ключевых слов",
        options=list(INDUSTRY_KEYWORDS.keys()),
        default=[]
    )

with col2:
    st.subheader("Дополнительные настройки")
    min_amount_d = st.number_input("Мин. сумма Form D ($)", value=500_000, step=100_000)
    min_amount_ca = st.number_input("Мин. сумма Form C/A ($)", value=100_000, step=50_000)
    max_amount = st.number_input("Макс. сумма ($)", value=20_000_000, step=1_000_000)
    
    custom_keywords = st.text_area(
        "Свои ключевые слова (через запятую)",
        placeholder="fintech, payments, healthtech"
    )

kw_list = []
for cat in selected_keywords_cat:
    kw_list.extend(INDUSTRY_KEYWORDS[cat])
if custom_keywords:
    kw_list.extend([k.strip() for k in custom_keywords.split(",") if k.strip()])
kw_q = build_query(kw_list) if kw_list else None

end_d = datetime.today().strftime("%Y-%m-%d")
start_d = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")

if st.button("🚀 Запустить парсинг", type="primary"):
    if not forms:
        st.error("Выберите хотя бы одну форму")
    else:
        with st.spinner("Парсим SEC EDGAR..."):
            try:
                ind_d = selected_industries if "D" in forms else None
                results = fetch_all(
                    forms, start_d, end_d, kw_q,
                    min_amount_d, min_amount_ca, max_amount,
                    ind_d, kw_list, keep_all=False, show_reasons=False, verbose=False
                )
            except Exception as e:
                st.error(f"Ошибка: {e}")
                results = []
        
        if results:
            df = pd.DataFrame(results)
            st.session_state.df = df  # Сохраняем в session_state
            
            st.success(f"Найдено {len(results)} компаний")
            
            st.subheader("Результаты")
            st.dataframe(
                df[["company_name", "form_type", "file_date", "industry_group", "offering_amount", "sold_amount"]],
                use_container_width=True,
                height=400
            )
            
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Скачать CSV",
                data=csv,
                file_name=f"sec_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
            
            if st.button("🔍 Find Contacts", type="secondary"):
                st.session_state.enrich = True
            
            if st.session_state.get("enrich", False):
                st.subheader("Enriching Contacts...")
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    client = load_api_client()
                    total = len(df)
                    for i, (index, row) in enumerate(df.iterrows()):
                        company_name = str(row.get("company_name") or "").strip()
                        cik = str(row.get("cik") or "").strip() or None
                        if company_name:
                            status_text.text(f"Processing {i+1}/{total}: {company_name[:50]}...")
                            enriched = enrich_row(client, company_name, cik, verbose=False)
                            df.at[index, "linkedin"] = enriched.get("linkedin")
                            df.at[index, "email"] = enriched.get("email")
                            df.at[index, "website"] = enriched.get("website")
                        progress_bar.progress((i + 1) / total)
                    
                    st.session_state.df = df
                    st.success("Enrichment completed!")
                    st.session_state.enrich = False
                    st.rerun()  # Перезагрузить страницу, чтобы обновить dataframe
                    
                except Exception as e:
                    st.error(f"Enrichment failed: {e}")
                    st.session_state.enrich = False
        else:
            st.warning("Ничего не найдено")