import streamlit as st
import pdfplumber
import fitz  # PyMuPDF
import re
import io

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(page_title="GDP Impact Evaluator", page_icon="✈️", layout="wide")

# --- CSS PERSONNALISÉ (Pour le look "Dashboard") ---
st.markdown("""
    <style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
        border: 1px solid #e0e0e0;
    }
    .big-number { font-size: 3em; font-weight: bold; color: #2c3e50; }
    .label { font-size: 1.2em; color: #7f8c8d; text-transform: uppercase; letter-spacing: 1px; }
    </style>
""", unsafe_allow_html=True)

# --- FONCTIONS MÉTIER (CACHÉES POUR LA PERF) ---

def extract_dep_scope(file) -> list:
    """Extrait les FIRs (C... ou Z...) de l'advisory"""
    scope = set()
    pattern = r'\b([CZ][A-Z0-9]{2,3})\b'
    
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                matches = re.findall(pattern, text)
                for m in matches:
                    if len(m) >= 3:
                        scope.add(m.upper())
    return sorted(list(scope))

def extract_time_window(file) -> str:
    """Trouve la ligne Filter(s)"""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            match = re.search(r"Filter\(s\):.*?between\s+(.*?)\s*$", text or "", re.MULTILINE | re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return "Non détectée"

def process_gdp(advisory_file, arrivals_file):
    # 1. Scope
    dep_scope = extract_dep_scope(advisory_file)
    
    # 2. Reset curseur fichier arrivals pour lecture
    arrivals_file.seek(0)
    time_window = extract_time_window(arrivals_file)
    
    # 3. Parsing & Annotation
    arrivals_file.seek(0)
    file_bytes = arrivals_file.read()
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    
    impacted_count = 0
    total_delay = 0
    scope_set = set(dep_scope)
    
    # Lecture tabulaire avec pdfplumber
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            table = page.extract_table()
            if not table: continue
            
            headers = [h.strip() if h else "" for h in table[0]]
            try:
                # Recherche des colonnes clés
                idx_acid = next(i for i, h in enumerate(headers) if "ACID" in h)
                idx_dcentr = next(i for i, h in enumerate(headers) if "DCENTR" in h)
                idx_delay = next(i for i, h in enumerate(headers) if "ProgramDelay" in h or "PgmDelay" in h)
            except:
                continue
                
            fitz_page = doc[page_idx]
            
            for row in table[1:]:
                if not row or len(row) <= max(idx_acid, idx_dcentr, idx_delay): continue
                
                acid = row[idx_acid]
                dcentr = row[idx_dcentr]
                delay_str = row[idx_delay]
                
                if dcentr and any(s in dcentr.upper() for s in scope_set):
                    # Extraction délai numérique
                    delay_val = 0
                    if delay_str:
                        clean = re.sub(r"[^0-9]", "", str(delay_str))
                        if clean: delay_val = int(clean)
                    
                    impacted_count += 1
                    total_delay += delay_val
                    
                    # Surlignage
                    text_instances = fitz_page.search_for(acid)
                    for inst in text_instances:
                        highlight = fitz_page.add_highlight_annot(inst)
                        highlight.set_colors(stroke=(1, 1, 0))
                        highlight.update()

    # 4. Calcul Impact
    avg = total_delay / impacted_count if impacted_count > 0 else 0
    if avg < 11: rating = "NIL"
    elif avg < 15: rating = "LOW"
    elif avg < 45: rating = "MODERATE"
    else: rating = "HIGH"

    # Export PDF
    out_buffer = io.BytesIO()
    doc.save(out_buffer)
    
    return {
        "scope": dep_scope,
        "window": time_window,
        "count": impacted_count,
        "total": total_delay,
        "avg": round(avg, 1),
        "rating": rating,
        "pdf": out_buffer.getvalue()
    }

# --- INTERFACE UTILISATEUR (UI) ---

st.title("🛫 GDP Impact Evaluator")
st.markdown("Outil d'analyse tactique ATM - **Aucune installation requise**")

col1, col2 = st.columns(2)
with col1:
    f_adv = st.file_uploader("1. Déposez l'Advisory GDP (PDF)", type="pdf")
with col2:
    f_arr = st.file_uploader("2. Déposez le Tableau des Arrivées (PDF)", type="pdf")

if st.button("Lancer l'Analyse", type="primary", disabled=not(f_adv and f_arr)):
    with st.spinner("Analyse des FIRs et calcul des délais..."):
        try:
            res = process_gdp(f_adv, f_arr)
            
            st.divider()
            
            # Affichage des KPIs
            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric("Vols Impactés", res["count"])
            kpi2.metric("Délai Moyen (min)", res["avg"], f"{res['total']} min total")
            
            color = "green" if res["rating"] == "NIL" else "orange" if res["rating"] == "LOW" else "red"
            kpi3.markdown(f"""
                <div style="text-align:center">
                    <div style="font-size:14px; color:gray">IMPACT</div>
                    <div style="font-size:32px; font-weight:bold; color:{color}">{res['rating']}</div>
                </div>
            """, unsafe_allow_html=True)
            
            st.divider()
            
            # Détails
            c_det1, c_det2 = st.columns(2)
            with c_det1:
                st.subheader("Paramètres détectés")
                st.write(f"**Fenêtre :** {res['window']}")
                st.write("**DEP SCOPE (FIRs) :**")
                st.code(" ".join(res["scope"]))
                
            with c_det2:
                st.subheader("Rapport")
                st.write("Le PDF a été annoté. Les vols du scope sont surlignés en jaune.")
                st.download_button(
                    label="📥 Télécharger le PDF Annoté",
                    data=res["pdf"],
                    file_name="GDP_Analysis_Result.pdf",
                    mime="application/pdf"
                )
                
        except Exception as e:
            st.error(f"Une erreur est survenue : {e}")
