import streamlit as st
import pdfplumber
import fitz  # PyMuPDF
import re
import io
import pandas as pd
from PIL import Image
import pytesseract
from pdf2image import convert_from_bytes

# Configuration Tesseract (parfois nécessaire sur le cloud)
# Sur Streamlit Cloud, c'est généralement automatique avec packages.txt

st.set_page_config(page_title="GDP OCR Evaluator", page_icon="🕵️", layout="wide")

st.markdown("""
    <style>
    .metric-card { background-color: #f0f2f6; padding: 15px; border-radius: 8px; border: 1px solid #ddd; }
    </style>
""", unsafe_allow_html=True)

# --- FONCTIONS OCR ---

def ocr_pdf(file_bytes):
    """Convertit un PDF scanné en texte brut via OCR"""
    images = convert_from_bytes(file_bytes)
    full_text = ""
    st.toast(f"OCR en cours sur {len(images)} pages... Patience.", icon="⏳")
    
    # Barre de progression car l'OCR est lent
    my_bar = st.progress(0)
    
    for i, image in enumerate(images):
        # On utilise une configuration pour garder la structure de la table si possible
        # --psm 6 assume un bloc de texte uniforme (bon pour les tableaux)
        text = pytesseract.image_to_string(image, config='--psm 6')
        full_text += text + "\n"
        my_bar.progress((i + 1) / len(images))
        
    my_bar.empty()
    return full_text

# --- FONCTIONS ANALYSE ---

def extract_dep_scope_ocr(text):
    scope = set()
    # Regex adaptée aux erreurs OCR (ex: 'ZNY' lu comme '2NY' ou 'ZNV')
    # On reste strict pour l'instant pour éviter le bruit
    pattern = r'\b([CZ][A-Z0-9]{2,3})\b'
    matches = re.findall(pattern, text)
    for m in matches:
        if len(m) >= 3 and m.upper() not in ["CYZ", "CZ"]: 
            scope.add(m.upper())
    return sorted(list(scope))

def parse_gdp_logic(text_content, scope):
    """
    Tentative de parsing ligne par ligne sur du texte OCR brut.
    C'est moins précis que pdfplumber mais c'est le seul moyen pour un scan.
    """
    impacted_rows = []
    total_delay = 0
    scope_set = set(scope)
    
    lines = text_content.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        # On cherche des lignes qui ressemblent à un vol
        # Ex de ligne: "ACA123 B737 CYUL ... 45 ..."
        parts = line.split()
        
        if len(parts) < 4: continue
        
        # Heuristique : Trouver le mot qui ressemble à un FIR du scope
        # C'est "dangereux" car l'ordre des colonnes est perdu, 
        # mais on cherche si la ligne contient UN des codes du scope.
        
        row_upper = line.upper()
        match_fir = next((s for s in scope_set if s in row_upper), None)
        
        if match_fir:
            # Si on trouve un FIR, on cherche un nombre qui ressemble à un délai
            # On assume que le délai est un nombre isolé, souvent vers la fin
            # On prend tous les nombres de la ligne
            numbers = re.findall(r'\b\d+\b', line)
            
            # Filtrage heuristique : un délai est souvent < 300 et > 0
            # Si on a plusieurs nombres (ex: vol 123, délai 45), on essaie de deviner
            delay_candidate = 0
            if numbers:
                # Souvent le délai est le dernier ou avant-dernier nombre
                # Risqué : ACID contient des chiffres.
                # On prend le dernier nombre de la ligne comme "Délai"
                try:
                    delay_candidate = int(numbers[-1])
                except:
                    pass
            
            # On stocke (avec Acid = premier mot supposé)
            impacted_rows.append({
                "acid": parts[0], 
                "dcentr": match_fir, # On met le FIR trouvé
                "delay": delay_candidate,
                "raw_line": line # Pour debug
            })
            total_delay += delay_candidate

    return impacted_rows, total_delay

# --- MAIN ---

st.title("🕵️ GDP Evaluator (Mode OCR pour Scans)")
st.info("Ce mode utilise l'intelligence artificielle (Tesseract) pour lire les images. C'est plus lent et moins précis que les PDF originaux.")

col1, col2 = st.columns(2)
f_adv = col1.file_uploader("1. Advisory (Scan)", type="pdf")
f_arr = col2.file_uploader("2. Arrivals (Scan)", type="pdf")

if st.button("Analyser les Scans"):
    if f_adv and f_arr:
        try:
            # 1. OCR Advisory
            with st.spinner("Lecture de l'Advisory (OCR)..."):
                adv_bytes = f_adv.read()
                adv_text = ocr_pdf(adv_bytes)
                dep_scope = extract_dep_scope_ocr(adv_text)
            
            if not dep_scope:
                st.error("Impossible de lire les FIRs (ZNY, etc) dans l'Advisory scanné. L'image est peut-être trop floue.")
                st.text_area("Texte lu :", adv_text[:500])
            else:
                st.success(f"Scope détecté : {dep_scope}")
                
                # 2. OCR Arrivals
                with st.spinner("Lecture du tableau (OCR) - Cela peut prendre 30s..."):
                    arr_bytes = f_arr.read()
                    arr_text = ocr_pdf(arr_bytes)
                
                # 3. Analyse Logique
                rows, total_min = parse_gdp_logic(arr_text, dep_scope)
                
                count = len(rows)
                avg = total_min / count if count > 0 else 0
                
                st.divider()
                k1, k2 = st.columns(2)
                k1.metric("Vols Identifiés", count)
                k2.metric("Retard Moyen (Estimé)", f"{round(avg, 1)} min")
                
                if count == 0:
                    st.warning("Aucun vol trouvé. Vérifiez la qualité du scan.")
                    with st.expander("Voir le texte brut lu par l'OCR"):
                        st.text(arr_text)
                else:
                    st.write("### Détail des vols détectés")
                    st.dataframe(pd.DataFrame(rows))
                    
        except Exception as e:
            st.error(f"Erreur technique : {e}")
            st.info("Vérifiez que 'packages.txt' contient bien 'tesseract-ocr' sur GitHub.")
    else:
        st.warning("Chargez les deux fichiers.")
