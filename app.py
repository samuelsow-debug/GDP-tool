import streamlit as st
import pdfplumber
import fitz  # PyMuPDF
import re
import io
import pandas as pd

# --- CONFIGURATION ---
st.set_page_config(page_title="GDP Evaluator V2", page_icon="✈️", layout="wide")

st.markdown("""
    <style>
    .metric-card { background-color: #f0f2f6; padding: 15px; border-radius: 8px; border: 1px solid #ddd; }
    .success { color: green; font-weight: bold; }
    .error { color: red; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# --- FONCTIONS ---

def clean_text(text):
    """Nettoie le texte (enlève les espaces multiples)"""
    if not text: return ""
    return re.sub(r'\s+', ' ', text).strip()

def extract_dep_scope(file):
    scope = set()
    # Regex améliorée : capture C... ou Z... même avec des caractères autour
    # Exclut les mots communs comme 'CZ' (Canadair?) ou codes trop courts
    pattern = r'\b([CZ][A-Z0-9]{2,3})\b'
    
    with pdfplumber.open(file) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text() or ""
            full_text += text + "\n"
            
            # Recherche
            matches = re.findall(pattern, text)
            for m in matches:
                # Filtrage supplémentaire pour éviter les faux positifs
                if len(m) >= 3 and m.upper() not in ["CENT", "CYZ", "CZ"]: 
                    scope.add(m.upper())
    
    return sorted(list(scope)), full_text

def extract_time_window(file):
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            # Regex plus souple pour la fenêtre horaire
            match = re.search(r"Filter.*?:.*?between\s+(.*?)\s*$", text or "", re.MULTILINE | re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return "Non détectée"

def find_column_index(headers, keywords):
    """Cherche l'index d'une colonne basée sur une liste de mots-clés possibles"""
    for idx, h in enumerate(headers):
        h_clean = str(h).upper().replace(" ", "")
        for k in keywords:
            if k in h_clean:
                return idx
    return None

def process_gdp(advisory_file, arrivals_file):
    logs = [] # Pour le debug
    
    # 1. Scope
    dep_scope, adv_text = extract_dep_scope(advisory_file)
    logs.append(f"🔍 SCOPE détecté ({len(dep_scope)}) : {dep_scope}")
    
    if not dep_scope:
        logs.append("⚠️ ATTENTION : Aucun FIR (ZNY, CZEG...) trouvé dans l'Advisory.")
    
    # 2. Time Window
    arrivals_file.seek(0)
    time_window = extract_time_window(arrivals_file)
    
    # 3. Parsing Table
    arrivals_file.seek(0)
    file_bytes = arrivals_file.read()
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    
    impacted_rows = []
    total_delay = 0
    scope_set = set(dep_scope)
    
    first_table_sample = None # Pour montrer à l'utilisateur si échec
    headers_detected = []
    
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            # Utilisation de 'text' strategy souvent meilleure pour les tableaux simples
            table = page.extract_table(table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"})
            
            if not table: 
                logs.append(f"Page {page_idx+1}: Pas de tableau détecté.")
                continue
            
            # Nettoyage des headers
            raw_headers = table[0]
            # On cherche les index (plus robuste)
            idx_acid = find_column_index(raw_headers, ["ACID", "AIRCRAFT", "CALLSIGN"])
            idx_dcentr = find_column_index(raw_headers, ["DCENTR", "DEP", "ORIGIN"])
            idx_delay = find_column_index(raw_headers, ["PGM", "DELAY", "PROGRAMDELAY", "MIN"])
            
            if page_idx == 0:
                headers_detected = raw_headers
                first_table_sample = table[:3] # Garde les 3 premières lignes pour debug
                logs.append(f"Colonnes identifiées -> ACID:{idx_acid}, DCENTR:{idx_dcentr}, DELAY:{idx_delay}")

            if idx_acid is None or idx_dcentr is None:
                continue # Skip cette page si pas de headers valides

            fitz_page = doc[page_idx]
            
            for row in table[1:]:
                # Vérification longueur ligne
                if not row or len(row) <= max(idx_acid, idx_dcentr, idx_delay or 0):
                    continue
                
                acid = row[idx_acid]
                dcentr = row[idx_dcentr]
                delay_str = row[idx_delay] if idx_delay is not None else "0"
                
                # Check match
                if dcentr:
                    dcentr_clean = dcentr.upper().strip()
                    # On vérifie si un des codes du scope est CONTENU dans la colonne DCENTR
                    # Ex: Scope "ZNY", DCENTR "KZNY" ou "ZNY" -> Match
                    match_found = any(s in dcentr_clean for s in scope_set)
                    
                    if match_found:
                        # Parsing délai
                        delay_val = 0
                        if delay_str:
                            clean_d = re.sub(r"[^0-9]", "", str(delay_str))
                            if clean_d: delay_val = int(clean_d)
                        
                        impacted_rows.append({"acid": acid, "dcentr": dcentr, "delay": delay_val})
                        total_delay += delay_val
                        
                        # Annotation
                        if acid:
                            text_instances = fitz_page.search_for(acid)
                            for inst in text_instances:
                                highlight = fitz_page.add_highlight_annot(inst)
                                highlight.set_colors(stroke=(1, 1, 0))
                                highlight.update()

    # Stats finales
    count = len(impacted_rows)
    avg = total_delay / count if count > 0 else 0
    
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
        "count": count,
        "total": total_delay,
        "avg": round(avg, 1),
        "rating": rating,
        "pdf": out_buffer.getvalue(),
        "logs": logs,
        "sample": first_table_sample,
        "headers": headers_detected,
        "raw_adv": adv_text[:500] # Debug advisory
    }

# --- UI ---

st.title("🛫 GDP Impact Evaluator V2 (Debug Mode)")

col1, col2 = st.columns(2)
f_adv = col1.file_uploader("1. Advisory PDF", type="pdf")
f_arr = col2.file_uploader("2. Arrivals PDF", type="pdf")

if st.button("Lancer l'Analyse", type="primary", disabled=not(f_adv and f_arr)):
    with st.spinner("Analyse en cours..."):
        try:
            res = process_gdp(f_adv, f_arr)
            
            # KPI
            st.divider()
            k1, k2, k3 = st.columns(3)
            k1.metric("Vols Impactés", res["count"])
            k2.metric("Moyenne Retard", f"{res['avg']} min")
            k3.metric("Impact", res["rating"])
            
            if res["count"] > 0:
                st.success(f"✅ Succès ! {res['count']} vols trouvés.")
                st.download_button("📥 Télécharger PDF Annoté", res["pdf"], "GDP_Result.pdf", "application/pdf")
            else:
                st.error("❌ Aucun vol n'a été détecté. Regardez les données de diagnostic ci-dessous.")

            # --- ZONE DE DEBUG ---
            with st.expander("🛠️ DIAGNOSTIC & DEBUG (Cliquez ici si 0 vols)", expanded=(res["count"]==0)):
                st.write("### 1. Extraction du DEP SCOPE (Advisory)")
                st.write(f"Codes trouvés : {res['scope']}")
                if not res['scope']:
                    st.warning("L'application n'a trouvé aucun code 'C...' ou 'Z...' dans l'advisory.")
                    st.text_area("Début du texte Advisory extrait :", res['raw_adv'], height=100)

                st.write("---")
                st.write("### 2. Lecture du Tableau (Arrivals)")
                st.write(f"En-têtes détectés : {res['headers']}")
                
                if res['sample']:
                    st.write("Aperçu des 3 premières lignes (vérifiez si les colonnes sont décalées) :")
                    st.dataframe(pd.DataFrame(res['sample']))
                else:
                    st.error("Aucun tableau n'a été extrait. Le PDF est peut-être une image scannée ?")

                st.write("---")
                st.write("### 3. Logs internes")
                for log in res['logs']:
                    st.text(log)

        except Exception as e:
            st.error(f"Erreur critique : {e}")
