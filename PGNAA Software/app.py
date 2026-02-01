import streamlit as st
import pandas as pd
import io
import datetime
import plotly.express as px
import plotly.graph_objects as go

def load_pgnaa(uploaded_file):
    """
    Loads PGNAA data with specific logic.
    """
    try:
        df = pd.read_csv(uploaded_file, encoding='latin1', low_memory=False)
        df.columns = df.columns.str.strip()
        
        if 'Date' in df.columns and 'Time' in df.columns:
            df = df.dropna(subset=['Date', 'Time'])
            df['Timestamp'] = pd.to_datetime(df['Date'].astype(str) + ' ' + df['Time'].astype(str), dayfirst=True, errors='coerce')
        elif 'Timestamp' in df.columns:
             df['Timestamp'] = pd.to_datetime(df['Timestamp'], dayfirst=True, errors='coerce')
        else:
            return None, "PGNAA file must have 'Date' and 'Time' columns."
            
        df = df.dropna(subset=['Timestamp'])
        df = df.drop_duplicates(subset=['Timestamp'], keep='first')
        df = df.sort_values('Timestamp')
        
        cols_to_keep = ['Timestamp'] + [c for c in df.columns if c.startswith('DB_') or c in ['SiO2', 'Al2O3', 'Fe2O3', 'CaO', 'MgO']]
        target_modularity = ['DB_LSF', 'DB_SR', 'DB_AR', 'LSF', 'SR', 'AR']
        cols_to_keep.extend([c for c in target_modularity if c in df.columns])
        cols_to_keep = list(dict.fromkeys(cols_to_keep))
        
        df = df[cols_to_keep]
        for col in df.columns:
            if col != 'Timestamp':
                df[col] = pd.to_numeric(df[col], errors='coerce')
                
        return df, None
    except Exception as e:
        return None, f"Error parsing PGNAA: {e}"

def load_xrf(uploaded_file):
    """
    Loads XRF data with specific logic.
    """
    try:
        content = uploaded_file.getvalue().decode('latin1', errors='ignore')
        lines = content.splitlines()
        header_row = 0
        for i, line in enumerate(lines[:20]):
            if "Date" in line and ("SiO2" in line or "Si" in line):
                header_row = i
                break
        
        sep = ',' if ',' in lines[header_row] else ';'
        uploaded_file.seek(0)
        df = pd.read_csv(uploaded_file, skiprows=header_row, sep=sep, encoding='latin1')
        df.columns = df.columns.str.strip()
        
        date_col = [c for c in df.columns if 'Date' in c][0]
        df['Timestamp'] = pd.to_datetime(df[date_col], dayfirst=True, errors='coerce')
        df = df.dropna(subset=['Timestamp'])
        
        target_cols = ['SiO2', 'Al2O3', 'Fe2O3', 'CaO', 'MgO', 'SO3', 'K2O', 'Na2O', 'LS', 'SR', 'AR']
        existing_cols = [c for c in target_cols if c in df.columns]
        final_df = df[['Timestamp'] + existing_cols].copy()
        
        for col in existing_cols:
            final_df[col] = pd.to_numeric(final_df[col], errors='coerce')
            
        return final_df, None
    except Exception as e:
        return None, f"Error parsing XRF: {e}"

def process_comparison(pgnaa_df, xrf_df, start_time_input, duration_mins, threshold):
    """
    Cadence-Aware Comparison with Excel Alignment.
    """
    results = []
    col_map = {
        'DB_Si': 'SiO2', 'DB_Al': 'Al2O3', 'DB_Fe': 'Fe2O3', 
        'DB_Ca': 'CaO', 'DB_Mg': 'MgO', 'DB_S': 'SO3',
        'DB_LSF': 'LS', 'DB_SR': 'SR', 'DB_AR': 'AR'
    }

    xrf_sorted = xrf_df.sort_values('Timestamp')
    
    for i in range(1, len(xrf_sorted)):
        prev_xrf = xrf_sorted.iloc[i-1]
        curr_xrf = xrf_sorted.iloc[i]
        
        cadence = (curr_xrf['Timestamp'] - prev_xrf['Timestamp']).total_seconds() / 60.0
        if abs(cadence - duration_mins) > 5:
            continue
            
        target_minute = start_time_input.minute
        t_end = curr_xrf['Timestamp'].replace(minute=target_minute, second=0, microsecond=0)
        if t_end > curr_xrf['Timestamp']:
            t_end -= pd.Timedelta(hours=1)
            
        t_start = t_end - pd.Timedelta(minutes=cadence)
        
        # PGNAA subset (INCLUSIVE to match Excel)
        mask_p = (pgnaa_df['Timestamp'] >= t_start) & (pgnaa_df['Timestamp'] <= t_end)
        pgnaa_slice = pgnaa_df[mask_p]
        
        if not pgnaa_slice.empty:
            if len(pgnaa_slice) < (cadence / 2 - 1):
                continue
                
            pgnaa_means = pgnaa_slice.mean(numeric_only=True)
            
            row_data = {
                'Time Interval Start': t_start,
                'Time Interval End': t_end,
                'XRF Timestamp': curr_xrf['Timestamp'],
                'Cadence (min)': int(cadence)
            }
            
            for p_col, x_col in col_map.items():
                if p_col in pgnaa_means and x_col in curr_xrf:
                    row_data[f'PGNAA {x_col} (Avg)'] = pgnaa_means[p_col]
                    row_data[f'XRF {x_col}'] = curr_xrf[x_col]
                    row_data[f'{x_col} Diff'] = pgnaa_means[p_col] - curr_xrf[x_col]

            # Modularity
            p_s, p_c, p_a, p_f = pgnaa_means.get('DB_Si'), pgnaa_means.get('DB_Ca'), pgnaa_means.get('DB_Al'), pgnaa_means.get('DB_Fe')
            x_s, x_c, x_a, x_f = curr_xrf.get('SiO2'), curr_xrf.get('CaO'), curr_xrf.get('Al2O3'), curr_xrf.get('Fe2O3')

            if 'DB_LSF' in pgnaa_means:
                row_data['PGNAA LSF (Avg)'] = pgnaa_means['DB_LSF']
            elif all(v is not None for v in [p_s, p_c, p_a, p_f]):
                row_data['PGNAA LSF (Avg)'] = p_c / (2.8*p_s + 1.18*p_a + 0.65*p_f) * 100
            if all(v is not None for v in [x_s, x_c, x_a, x_f]):
                row_data['XRF LSF'] = x_c / (2.8*x_s + 1.18*x_a + 0.65*x_f) * 100
                if 'PGNAA LSF (Avg)' in row_data: row_data['LSF Diff'] = row_data['PGNAA LSF (Avg)'] - row_data['XRF LSF']

            if 'DB_SR' in pgnaa_means:
                row_data['PGNAA SR (Avg)'] = pgnaa_means['DB_SR']
            elif all(v is not None for v in [p_s, p_a, p_f]):
                row_data['PGNAA SR (Avg)'] = p_s / (p_a + p_f)
            if all(v is not None for v in [x_s, x_a, x_f]):
                row_data['XRF SR'] = x_s / (x_a + x_f)
                if 'PGNAA SR (Avg)' in row_data: row_data['SR Diff'] = row_data['PGNAA SR (Avg)'] - row_data['XRF SR']

            if 'DB_AR' in pgnaa_means:
                row_data['PGNAA AF (Avg)'] = pgnaa_means['DB_AR']
            elif all(v is not None for v in [p_a, p_f]):
                row_data['PGNAA AF (Avg)'] = p_a / p_f
            if all(v is not None for v in [x_a, x_f]):
                row_data['XRF AF'] = x_a / x_f
                if 'PGNAA AF (Avg)' in row_data: row_data['AF Diff'] = row_data['PGNAA AF (Avg)'] - row_data['XRF AF']

            results.append(row_data)
            
    return pd.DataFrame(results)

@st.cache_data
def load_lab_times(uploaded_file):
    """
    Extracts arrival minutes.
    """
    try:
        df = pd.read_csv(uploaded_file, encoding='latin1') if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        df.columns = df.columns.astype(str).str.strip()
        minutes_list = []
        for col in df.columns:
            if any(x in col.lower() for x in ['id', 'name', 'value', 'data', 'pgnaa', 'sio2']): continue
            col_data = df[col].astype(str).dropna()
            matches = col_data.str.extract(r'(\d{1,2}):(\d{2})')
            if not matches.dropna().empty:
                found_mins = pd.to_numeric(matches[1], errors='coerce').dropna()
                minutes_list.extend(found_mins.tolist())
                if len(found_mins) > 2: break
        if not minutes_list: return None, "No time data found."
        return pd.DataFrame({'ArrivalMinute': minutes_list}), None
    except Exception as e:
        return None, f"Error: {e}"

@st.cache_data
def analyze_arrival_patterns(lab_times_df):
    """
    Analyzes arrival patterns.
    """
    try:
        minutes = lab_times_df['ArrivalMinute']
        if minutes.empty: return None, "No data."
        mode_res = minutes.mode()
        if mode_res.empty: return None, "No mode."
        most_common = int(mode_res[0])
        recommended = (most_common - 20) % 60
        count = int(minutes.value_counts().loc[most_common])
        return {'mode': most_common, 'recommended': recommended, 'count': count, 'freq': minutes.value_counts().sort_index()}, None
    except Exception as e:
        return None, f"Error: {e}"

def highlight_rows(row, threshold):
    styles = ['' for _ in row.index]
    for i, col in enumerate(row.index):
        if col.endswith('Diff'):
            try:
                val = abs(row[col])
                if val <= (threshold * 0.5): bg_color, font_color = 'rgba(130, 188, 0, 0.1)', '#4b5563'
                elif val <= threshold: bg_color, font_color = 'rgba(255, 193, 7, 0.2)', '#856404'
                else: bg_color, font_color = 'rgba(220, 38, 38, 0.15)', '#991b1b'
                styles[i] = f'background-color: {bg_color}; color: {font_color}; font-weight: 500;'
            except: pass
    return styles

def main():
    st.set_page_config(page_title="Holcim PGNAA vs XRF", layout="wide", page_icon="🔬")
    
    # --- PREMIUM CSS (Holcim Branding) ---
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        
        :root {
            --holcim-navy: #002f6c;
            --holcim-blue: #00a9e0;
            --holcim-green: #82bc00;
            --bg-gray: #f8fafc;
        }
        
        .stApp { background-color: var(--bg-gray); font-family: 'Inter', sans-serif; }
        
        /* Sidebar Styling */
        [data-testid="stSidebar"] {
            background-image: linear-gradient(180deg, #002f6c 0%, #001a3d 100%);
            border-right: 1px solid rgba(255,255,255,0.1);
        }
        
        /* Specific sidebar text - ensure maximum visibility */
        [data-testid="stSidebar"] .stMarkdown, 
        [data-testid="stSidebar"] p, 
        [data-testid="stSidebar"] h1, 
        [data-testid="stSidebar"] h2, 
        [data-testid="stSidebar"] h3, 
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] .stCaption,
        [data-testid="stSidebar"] small,
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { 
            color: #ffffff !important; 
            font-weight: 500 !important;
        }
        
        /* Fix for uploaded file names invisibility */
        [data-testid="stSidebar"] [data-testid="stFileUploaderFileName"],
        [data-testid="stSidebar"] [data-testid="stFileUploaderFileData"] * {
            color: #ffffff !important;
        }
        
        /* Ensure inputs are readable */
        [data-testid="stSidebar"] input, 
        [data-testid="stSidebar"] select, 
        [data-testid="stSidebar"] .stSelectbox div {
            color: #0f172a !important; 
            font-weight: 600 !important;
        }
        
        /* File Uploader styling */
        [data-testid="stFileUploadDropzone"] {
            background-color: rgba(255, 255, 255, 0.1) !important;
            border: 2px dashed rgba(255, 255, 255, 0.3) !important;
            border-radius: 10px !important;
        }
        [data-testid="stFileUploadDropzone"] div div span {
            color: #ffffff !important;
        }
        [data-testid="stFileUploadDropzone"] button {
            background: white !important;
            color: #002f6c !important;
        }
        
        /* Header */
        .main-header {
            background: linear-gradient(90deg, #002f6c 0%, #00a9e0 50%, #82bc00 100%);
            padding: 2.5rem;
            border-radius: 12px;
            color: white;
            margin-bottom: 2rem;
            box-shadow: 0 10px 25px rgba(0,47,108,0.15);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .main-header h1 { margin: 0; font-size: 2.2rem; font-weight: 700; letter-spacing: -0.5px; }
        .main-header p { margin: 5px 0 0 0; opacity: 0.9; font-weight: 400; }
        
        /* Cards */
        .metric-card {
            background: white;
            padding: 1.5rem;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .metric-card:hover { transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); }
        
        /* Tabs */
        .stTabs [data-baseweb="tab-list"] { gap: 10px; background-color: rgba(0,0,0,0.02); padding: 8px; border-radius: 8px; }
        .stTabs [data-baseweb="tab"] {
            background-color: white;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
            padding: 8px 16px;
            font-weight: 500;
        }
        .stTabs [aria-selected="true"] {
            background: var(--holcim-navy) !important;
            color: white !important;
            border-color: var(--holcim-navy) !important;
        }
        
        /* Buttons */
        .stButton>button {
            background: linear-gradient(135deg, #002f6c 0%, #00a9e0 100%);
            color: white;
            border: none;
            padding: 0.5rem 1.5rem;
            border-radius: 8px;
            font-weight: 600;
            transition: all 0.3s;
        }
        .stButton>button:hover { opacity: 0.9; transform: scale(1.02); }
    </style>
    """, unsafe_allow_html=True)
    
    # Custom Header
    st.markdown("""
        <div class="main-header">
            <div>
                <h1>PGNAA ⇆ XRF Smart Analyzer</h1>
                <p>Industrial performance dashboard for <b>Holcim Bulgaria</b></p>
            </div>
            <div style="font-size: 1.5rem; font-weight: bold; border-left: 3px solid rgba(255,255,255,0.3); padding-left: 20px;">
                HOLCIM
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    # --- SIDEBAR ---
    with st.sidebar:
        st.header("⚙️ Settings")
        # Custom Time Input (Text-based for easier keyboard entry)
        start_time_str = st.text_input("Start Time (HH:MM)", value="08:00")
        try:
            start_time = pd.to_datetime(start_time_str, format='%H:%M').time()
        except:
            st.error("Invalid format! Using 08:00")
            start_time = datetime.time(8, 0)
        duration = st.selectbox("Duration (min)", options=[120, 60], index=0)
        threshold = st.number_input("Alert Threshold", min_value=0.0, value=0.5, step=0.1)
        
        st.markdown("---")
        st.caption("Optional: Lab Diagnostic")
        lab_times_file = st.file_uploader("📂 Upload Lab Times", type=['csv', 'xlsx', 'xls'])
        if lab_times_file:
            df_lab, err_l = load_lab_times(lab_times_file)
            if not err_l:
                insights, err_i = analyze_arrival_patterns(df_lab)
                if insights:
                    st.success(f"📈 Peak at **:{insights['mode']:02d}** min")
                    st.info(f"🔢 Count: **{insights['count']}**")
                    st.warning(f"💡 Start at **:{insights['recommended']:02d}** min")
        
        st.markdown("---")
        st.caption("Required: Analysis Files")
        file_a = st.file_uploader("PGNAA (result2.CSV)", type=['csv'])
        file_b = st.file_uploader("XRF (Raw meal112.csv)", type=['csv'])
    
    if file_a and file_b:
        df_p, err_a = load_pgnaa(file_a)
        df_x, err_b = load_xrf(file_b)
        
        if err_a or err_b:
            st.error(f"Error: {err_a or err_b}"); return

        with st.spinner("Analyzing data streams..."):
            res = process_comparison(df_p, df_x, start_time, duration, threshold)
            
            if res.empty:
                st.warning("No matches found. Cross-check your timestamps.")
            else:
                st.markdown("### 🎯 Key Performance Indicators")
                k_cols = st.columns(4)
                
                with k_cols[0]:
                    st.markdown(f"""<div class="metric-card"><small>Matched Samples</small><h2>{len(res)}</h2><p style='color: #82bc00; font-size: 0.8rem; font-weight: 600;'>↑ Active Analysis</p></div>""", unsafe_allow_html=True)
                with k_cols[1]:
                    bias = res['SiO2 Diff'].mean() if 'SiO2 Diff' in res.columns else 0
                    st.markdown(f"""<div class="metric-card"><small>Avg SiO2 Bias</small><h2>{bias:.2f}</h2><p style='color: {"#002f6c" if abs(bias) < threshold else "#dc2626"}; font-size: 0.8rem; font-weight: 600;'>{"Within limit" if abs(bias) < threshold else "Out of limit"}</p></div>""", unsafe_allow_html=True)
                with k_cols[2]:
                    compliance = (sum(1 for _, r in res.iterrows() if not any(abs(r[c]) > threshold for c in res.columns if c.endswith('Diff'))) / len(res)) * 100
                    st.markdown(f"""<div class="metric-card"><small>Compliance Rate</small><h2>{compliance:.1f}%</h2><p style='color: #00a9e0; font-size: 0.8rem; font-weight: 600;'>Target: 95%</p></div>""", unsafe_allow_html=True)
                with k_cols[3]:
                    st.markdown(f"""<div class="metric-card"><small>Operational Mode</small><h2>Manual</h2><p style='color: #82bc00; font-size: 0.8rem; font-weight: 600;'>● System Online</p></div>""", unsafe_allow_html=True)
                
                st.markdown("---")
                
                tab1, tab2, tab3 = st.tabs(["📊 Visual Analysis", "📋 Detailed Data", "📉 Correlation Check"])
                
                with tab1:
                    available_elements = [col.replace(' Diff', '') for col in res.columns if col.endswith(' Diff')]
                    if available_elements:
                        selected_el = st.selectbox("Element Navigator", options=available_elements, index=0)
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(x=res['Time Interval Start'], y=res[f'PGNAA {selected_el} (Avg)'], mode='lines+markers', name='PGNAA', line=dict(color='#002f6c', width=3)))
                        fig.add_trace(go.Scatter(x=res['Time Interval Start'], y=res[f'XRF {selected_el}'], mode='markers', name='XRF', marker=dict(color='#82bc00', size=12, symbol='cross', line=dict(width=2, color='white'))))
                        fig.update_layout(title=f"<b>{selected_el} Trend Analysis</b>", template="plotly_white", margin=dict(l=20, r=20, t=50, b=20))
                        st.plotly_chart(fig, use_container_width=True)
                
                with tab2:
                    st.subheader("Raw Comparison Data")
                    # Set precision to 2 for cleaner look
                    styled = res.style.apply(lambda r: highlight_rows(r, threshold), axis=1).format(precision=2)
                    st.dataframe(styled, use_container_width=True, height=550)
                    st.download_button("📥 Export CSV Report", res.to_csv(index=False).encode('utf-8'), "holcim_report.csv", "text/csv")
                    
                with tab3:
                    if 'PGNAA SiO2 (Avg)' in res.columns:
                        # Manual Correlation Chart (No statsmodels dependency)
                        fig_corr = px.scatter(
                            res, x='XRF SiO2', y='PGNAA SiO2 (Avg)', 
                            title="<b>Accuracy Correlation (Lab vs Analyzer)</b>",
                            labels={'XRF SiO2': 'Lab (Actual)', 'PGNAA SiO2 (Avg)': 'Analyzer (Predicted)'}
                        )
                        
                        # Add a 1:1 Reference Line
                        min_v = min(res['XRF SiO2'].min(), res['PGNAA SiO2 (Avg)'].min())
                        max_v = max(res['XRF SiO2'].max(), res['PGNAA SiO2 (Avg)'].max())
                        fig_corr.add_shape(
                            type="line", x0=min_v, y0=min_v, x1=max_v, y1=max_v,
                            line=dict(color="#82bc00", width=2, dash="dash")
                        )
                        
                        fig_corr.update_traces(marker=dict(color='#002f6c', size=12, opacity=0.8, line=dict(width=1, color='white')))
                        fig_corr.update_layout(template="plotly_white")
                        st.plotly_chart(fig_corr, use_container_width=True)
    else:
        st.info("👋 Welcome! Please upload your data files in the sidebar to begin analysis.")

if __name__ == "__main__":
    main()
