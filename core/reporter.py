import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from fpdf import FPDF
import os
import logging
import re
import platform
from datetime import datetime
from fpdf.fonts import FontFace

logger = logging.getLogger(__name__)

class SAPReporter:
    def __init__(self, output_dir):
        self.output_dir = output_dir

    def format_bytes(self, size_bytes):
        if size_bytes == 0: return "0B"
        units = ("B", "K", "M", "G", "T")
        i = 0
        while size_bytes >= 1024 and i < len(units)-1:
            size_bytes /= 1024
            i += 1
        return f"{size_bytes:,.1f}{units[i]}"

    def generate_unified_axis_chart(self, df, windows):
        """Generates a chart with CPU and Memory on a SINGLE 0-100% Y-axis as per guideline."""
        NAVY_HEX = '#003057'
        ORANGE_HEX = '#ef811d'
        RED_ALERT_HEX = '#d32f2f'
        
        plt.style.use('bmh')
        fig, ax = plt.subplots(figsize=(12, 6))
        
        # SINGLE Y-axis for both (0% ~ 100%)
        ax.set_ylim(0, 105) # Extra space for labels
        ax.plot(df['TIMESTAMP'], df['CPU'], label='CPU (%)', color=NAVY_HEX, linewidth=1.5, alpha=0.9)
        if 'MEMORY_PCT' in df.columns:
            ax.plot(df['TIMESTAMP'], df['MEMORY_PCT'], label='Memory (%)', color=ORANGE_HEX, linewidth=1.5, alpha=0.7, linestyle='--')
            
        # Chart Labels in ENGLISH as per Guideline
        ax.set_ylabel('Resource Utilization (%)', fontsize=12, fontweight='bold', color=NAVY_HEX)
        ax.set_xlabel('Analysis Time (KST)', fontsize=10)
        ax.set_title(f"SAP Resource Trend Analysis - {df['TIMESTAMP'].iloc[0].strftime('%Y-%m-%d')}", fontsize=16, fontweight='bold', pad=20)
        
        # Highlight Peaks
        for w in windows:
            ax.axvspan(w['start'], w['end'], color=RED_ALERT_HEX, alpha=0.1)
            
        ax.legend(loc='upper right', frameon=True, facecolor='white', framealpha=0.9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        plt.xticks(rotation=0)
        
        plt.tight_layout()
        run_dt = datetime.now().strftime('%H%M%S')
        chart_path = os.path.join(self.output_dir, f"sap_unified_trend_{run_dt}.png")
        plt.savefig(chart_path, dpi=120)
        plt.close()
        return chart_path

    def create_pdf_report(self, analysis_data, stats, windows, top_sql, top_locks, global_top=None, output_filename="LATEST_SAP_ANALYSIS_v4.pdf"):
        """Generates a Premium Executive SAP Performance Report strictly following 1-8 step layout."""
        pdf = FPDF(orientation='P', unit='mm', format='A4')
        pdf.set_margins(10, 15, 10)
        
        # Font Configuration
        font_name = "Helvetica"
        font_paths = []
        if platform.system() == "Darwin":
            font_paths.append("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
        elif platform.system() == "Windows":
            font_paths.extend([
                r"C:\Windows\Fonts\ARIALUNI.TTF",
                r"C:\Windows\Fonts\arialuni.ttf",
                r"C:\Windows\Fonts\ARIALUN.TTF"
            ])
        else:
            font_paths.extend([
                "/usr/share/fonts/truetype/msttcorefonts/Arial Unicode.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ])

        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    pdf.add_font("ArialUnicode", "", font_path, uni=True)
                    pdf.add_font("ArialUnicode", "B", font_path, uni=True)
                    font_name = "ArialUnicode"
                except Exception:
                    font_name = "Helvetica"
                break

        NAVY = (0, 48, 87)
        TEXT_COL = (33, 37, 41)
        GRAY_LINE = (200, 200, 200)

        def ensure_space(h):
            # Reduced threshold from 280 to 275 to prevent edge-case page breaks
            if pdf.get_y() + h > 275:
                pdf.add_page()
                return True
            return False

        # --- TITLE (제목 - 번호 제외) ---
        pdf.add_page()
        pdf.set_font(font_name, "B", 22)
        pdf.set_text_color(*NAVY)
        report_title = f"{stats.get('date', 'N/A')} 이마트 SAP DB 서버 모니터링 리포트"
        pdf.cell(0, 20, report_title, ln=True, align='C')
        pdf.set_draw_color(*NAVY)
        pdf.line(10, 35, 200, 35)
        pdf.ln(10)

        # --- 1. Summary (요약) ---
        ensure_space(40)
        pdf.set_font(font_name, "B", 14)
        pdf.cell(0, 10, "1. Summary (요약)", ln=True)
        pdf.set_font(font_name, "", 10)
        pdf.set_text_color(*TEXT_COL)
        
        # AI Insight integration
        insights = analysis_data.get('ai_insights', '')
        # summary_match = re.search(r'1\.\s*Summary.*?:\n(.*?)\n\d\.', insights, re.DOTALL)
        summary_match = re.search(r'1\.\s*Summary\s*[:*]*\s*(.*?)(?=\n\s*[*#]*\s*\d\.|\[|$)', insights, re.DOTALL)
        summary_text = summary_match.group(1).strip() if summary_match else "AI 연결 안됨 (데이터 기반 요약 기능 비활성화)"
        # More robust removal: if the first line contains 'Summary' or '요약', remove that whole line.
        summary_lines = summary_text.split('\n')
        if summary_lines and ('Summary' in summary_lines[0] or '요약' in summary_lines[0]):
            summary_text = '\n'.join(summary_lines[1:]).strip()
            
        summary_text = re.sub(r'\n\s*---+\s*$', '', summary_text).strip()
        pdf.multi_cell(190, 7, summary_text, border=0)
        pdf.ln(4)

        # --- 2. 일별 CPU/메모리 요약 테이블 ---
        ensure_space(50)
        pdf.set_font(font_name, "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 10, "2. 일별 CPU/메모리 요약 테이블", ln=True)
        pdf.set_font(font_name, "", 9)
        
        with pdf.table(col_widths=(25, 20, 20, 20, 20, 25, 30, 15, 15), 
                      text_align=("C", "C", "C", "C", "C", "C", "C", "C", "C"),
                      line_height=8.0, headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
            h = table.row()
            for header in ["날짜", "CPU최소", "CPU평균", "CPU최대", "CPU95", "고부하횟수", "메모리평균", "표본수", "비고"]:
                h.cell(header)
            
            row = table.row()
            row.cell(str(stats.get('date', 'N/A')))
            row.cell(f"{stats.get('cpu_min', 0):.1f}%")
            row.cell(f"{stats.get('cpu_avg', 0):.1f}%")
            row.cell(f"{stats.get('cpu_max', 0):.1f}%")
            row.cell(f"{stats.get('cpu_p95', 0):.1f}%")
            row.cell(f"{stats.get('high_load_count', 0)}회")
            row.cell(f"{stats.get('mem_avg_pct', 0):.1f}%")
            row.cell(str(stats.get('sample_count', 0)))
            row.cell("주의" if stats.get('cpu_max', 0) > 85 else "정상")
        
        pdf.set_font(font_name, "", 7)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 6, "* CPU95는 95th percentile 기준이며, 메모리평균은 Allocation Limit 대비 사용율입니다.", ln=True)
        pdf.ln(5)

        # --- 3. 차트 출력 및 해석 ---
        ensure_space(110)
        pdf.set_font(font_name, "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 10, "3. 차트 출력 및 해석", ln=True)
        
        chart_path = analysis_data.get('chart_path')
        if chart_path and os.path.exists(chart_path):
            pdf.image(chart_path, x=15, w=180)
            pdf.ln(5)
            # Interpretation Text (Try to get from AI Section 3)
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(*TEXT_COL)
            chart_match = re.search(r'3\.\s*차트 해석.*?:\n(.*?)\n\d\.', insights, re.DOTALL)
            chart_text = chart_match.group(1).strip() if chart_match else "측정 시간 동안의 시스템 리소스 트렌드입니다. 빨간색 하이라이트 영역은 지침에 따라 선정된 주요 피크 구간을 나타냅니다. CPU와 메모리가 동시에 급증하는 구간은 배치 연산 또는 대량 데이터 처리가 의심됩니다."
            pdf.multi_cell(190, 6, chart_text, border="L")
        pdf.ln(10)

        # --- 4. Peak 구간 요약 테이블 ---
        ensure_space(50)
        pdf.set_font(font_name, "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 10, "4. Peak 구간 요약 테이블", ln=True)
        
        if windows:
            pdf.set_font(font_name, "", 8)
            with pdf.table(col_widths=(60, 40, 40, 50), text_align=("C", "C", "C", "L"), line_height=6.0,
                          headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
                h = table.row()
                for header in ["시간구간", "평균 CPU", "최대 CPU", "탐지근거"]:
                    h.cell(header)
                for w in windows:
                    r = table.row()
                    r.cell(f"{w['start'].strftime('%H:%M')} ~ {w['end'].strftime('%H:%M')}")
                    r.cell(f"{w['avg_cpu']:.1f}%")
                    r.cell(f"{w['max_cpu']:.1f}%")
                    r.cell(w.get('reason', 'Critical Load Detected'))
            pdf.ln(3)
            # Add official Peak definition sentence
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(*TEXT_COL)
            off_str = "※ Peak 구간 선정 기준: CPU 사용률이 상위 95퍼센타일 이상인 시점을 Peak 후보로 정의한 후, Peak 후보 시점 간 시간 간격이 5분 이내인 경우 동일 Peak 이벤트로 병합하였습니다. (이때 비교 기준은 전체 시계열이 아닌, Peak 조건을 만족한 시점 간의 연속성입니다.)"
            pdf.multi_cell(190, 5, off_str, border=0)
            
        pdf.ln(8)

        # --- 5. Peak Load 구간별 SQL 영향도 분석 ---
        ensure_space(60)
        pdf.set_font(font_name, "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 10, "5. Peak Load 구간별 SQL 영향도 분석", ln=True)
        
        pdf.set_font(font_name, "", 9)
        pdf.set_text_color(*TEXT_COL)
        sql_desc = "※ Peak Window 내 수행된 SQL 중, 누적 실행 시간 상위 10% 또는 평균 실행 시간 1초 이상인 SQL만을 분석 대상으로 선정하였습니다."
        pdf.multi_cell(190, 6, sql_desc, ln=True)
        pdf.ln(2)
        
        if top_sql is not None and not top_sql.empty:
            pdf.set_font(font_name, "", 7)
            # Column Order: 시간구간, 프로그램, 쿼리, 횟수, 실행시간(s), 메모리, 설명
            with pdf.table(col_widths=(20, 25, 45, 15, 25, 25, 35), line_height=5.0,
                          headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
                h = table.row()
                for header in ["시간구간", "프로그램", "대표 쿼리", "횟수", "실행시간", "메모리", "설명"]:
                    h.cell(header)
                last_period = None
                for _, r in top_sql.iterrows():
                    curr_period = str(r.get('PEAK_PERIOD', 'N/A'))
                    display_period = "" if curr_period == last_period else curr_period
                    last_period = curr_period

                    row = table.row()
                    row.cell(display_period)
                    row.cell(str(r.get('PROGRAM_LABEL', 'N/A'))[:25])
                    row.cell(str(r.get('SQL_LABEL', 'N/A'))[:100])
                    row.cell(f"{int(r.get('EXEC_COUNT_peak', 0)):,}")
                    time_str = f"총 {r.get('TOTAL_EXEC_TIME_peak', 0):,.1f}s\n평균 {r.get('AVG_EXEC_TIME_peak', 0):,.1f}s\n최대 {r.get('MAX_EXEC_TIME_peak', 0):,.1f}s"
                    row.cell(time_str)
                    mem_str = f"총 {self.format_bytes(r.get('TOTAL_MEM_peak', 0))}\n평균 {self.format_bytes(r.get('AVG_MEM_peak', 0))}\n최대 {self.format_bytes(r.get('MAX_MEM_peak', 0))}"
                    row.cell(mem_str)
                    row.cell(str(r.get('CAUSE', '-')))
            pdf.ln(2)
            # AI SQL Commentary
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(*TEXT_COL)
            # sql_comm_match = re.search(r'5\.\s*SQL 영향도 분석 Commentary.*?:\n(.*?)\n\s*[*#]*\s*\d\.', insights, re.DOTALL)
            sql_comm_match = re.search(r'5\.\s*SQL 영향도 분석 Commentary\s*[:*]*\s*(.*?)(?=\n\s*[*#-]*\s*6\.|\[|$)', insights, re.DOTALL)
            sql_comm = sql_comm_match.group(1).strip() if sql_comm_match else "상위 SQL 실행 통계에 기반하여 AI가 부하 패턴을 분석 중입니다."
            sql_comm = re.sub(r'\n\s*---+\s*$', '', sql_comm).strip()
            pdf.multi_cell(190, 6, sql_comm, border="L")
        pdf.ln(8)

        # --- 6. 서비스 대기(Lock Wait) 분석 ---
        ensure_space(60)
        pdf.set_font(font_name, "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 10, "6. 서비스 대기(Lock Wait) 분석", ln=True)
        
        pdf.set_font(font_name, "", 9)
        pdf.set_text_color(*TEXT_COL)
        lock_desc = "※ Lock Wait Ratio 0.3 이상이며 누적 Lock 대기 시간이 상위 10%에 해당하는 SQL을 중심으로 영향도를 평가하였습니다."
        pdf.multi_cell(190, 6, lock_desc, ln=True)
        pdf.ln(2)
        
        if top_locks is not None and not top_locks.empty:
            pdf.set_font(font_name, "", 7)
            with pdf.table(col_widths=(20, 25, 45, 15, 25, 25, 35), line_height=5.0,
                          headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
                h = table.row()
                for header in ["시간구간", "프로그램", "대표 쿼리", "횟수", "락 대기(s)", "메모리", "원인 추정"]:
                    h.cell(header)
                last_period = None
                for _, r in top_locks.iterrows():
                    curr_period = str(r.get('PEAK_PERIOD', 'N/A'))
                    display_period = "" if curr_period == last_period else curr_period
                    last_period = curr_period

                    row = table.row()
                    row.cell(display_period)
                    row.cell(str(r.get('PROGRAM_LABEL', 'N/A'))[:25])
                    row.cell(str(r.get('SQL_LABEL', 'N/A'))[:100])
                    row.cell(f"{int(r.get('LOCK_COUNT', 0)):,}")
                    row.cell(f"{r.get('TOTAL_LOCK_WAIT_SEC_peak', 0):,.1f}s")
            pdf.ln(2)
            # AI Lock Commentary
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(*TEXT_COL)
            # lock_comm_match = re.search(r'6\.\s*서비스 대기\(Lock Wait\) 분석 Commentary.*?:\n(.*?)\n\s*[*#]*\s*\d\.', insights, re.DOTALL)
            lock_comm_match = re.search(r'6\.\s*서비스 대기\(Lock Wait\) 분석 Commentary\s*[:*]*\s*(.*?)(?=\n\s*[*#-]*\s*7\.|\[|$)', insights, re.DOTALL)
            lock_comm = lock_comm_match.group(1).strip() if lock_comm_match else "탐지된 Lock 경합 데이터에 기반하여 AI가 병목 원인을 분석 중입니다."
            lock_comm = re.sub(r'\n\s*---+\s*$', '', lock_comm).strip()
            pdf.multi_cell(190, 6, lock_comm, border="L")
        else:
            pdf.set_font(font_name, "", 10)
            pdf.cell(0, 10, "분석 시간 내 유의미한 Lock 지연 현상이 발견되지 않았습니다.", ln=True)
        pdf.ln(10)

        # --- 7. 종합 진단 및 기술적 제언 ---
        ensure_space(100)
        pdf.set_font(font_name, "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 10, "7. 종합 진단 및 기술적 제언", ln=True)
        pdf.ln(2)
        
        # 7-1. 원인 및 해결방안 요약 Table (from Guideline v5)
        rca_list = []
        source_df = global_top if global_top is not None and not global_top.empty else top_sql
        
        if source_df is not None and not source_df.empty:
            for i, r in enumerate(source_df.head(10).iterrows()):
                r_data = r[1]
                rca_list.append({
                    'rank': i+1, 'prog': r_data.get('PROGRAM_LABEL', 'N/A'),
                    'sql': r_data.get('SQL_LABEL', 'N/A'), 'count': f"{int(r_data.get('EXEC_COUNT_peak', 0)):,}",
                    'time': f"{r_data.get('MAX_EXEC_TIME_peak', 0):.1f}s / {r_data.get('AVG_EXEC_TIME_peak', 0):.2f}s",
                    'mem': self.format_bytes(r_data.get('TOTAL_MEM_peak', 0)),
                    'action': r_data.get('ACTION', 'SQL 튜닝 및 인덱스 최적화 권고')
                })
        
        if rca_list:
            pdf.set_font(font_name, "", 7)
            with pdf.table(col_widths=(12, 25, 48, 15, 25, 25, 40), line_height=7.0,
                          headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
                h = table.row()
                for header in ["순위", "프로그램", "대표 SQL", "횟수", "실행시간(M/A)", "메모리", "해결방안"]:
                    h.cell(header)
                for item in rca_list:
                    row = table.row()
                    row.cell(str(item['rank'])); row.cell(str(item['prog'])[:25]); row.cell(str(item['sql'])[:80])
                    row.cell(item['count']); row.cell(item['time']); row.cell(item['mem']); row.cell(item['action'])
        pdf.ln(5)

        # 7-2. CPU 부하 원인 유형 (AI Driven)
        pdf.set_font(font_name, "B", 11)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 8, "[ CPU 부하 원인 유형 ]", ln=True)
        pdf.set_font(font_name, "", 10)
        pdf.set_text_color(*TEXT_COL)
        
        cause_type_match = re.search(r'\[부하 원인 유형\]\s*[:*]*\s*(.*?)(?=\n\s*[*#]*\s*\[개선 포인트\]|\n\s*[*#]*\s*\[최종 진단\]|$)', insights, re.DOTALL)
        cause_type_ai = cause_type_match.group(1).strip() if cause_type_match else ""
            
        is_batch = "[V]" if "배치" in cause_type_ai else "[ ]"
        is_lock = "[V]" if "동시성" in cause_type_ai or "락" in cause_type_ai else "[ ]"
        is_agg = "[V]" if "집계" in cause_type_ai or "스캔" in cause_type_ai else "[ ]"
        
        pdf.cell(0, 7, f"  {is_batch} 배치성 부하: 정기 배치 집중", ln=True)
        pdf.cell(0, 7, f"  {is_lock} 동시성(락): NRIV, COSP_BAK Lock 경합", ln=True)
        pdf.cell(0, 7, f"  {is_agg} 대량 집계 / 전체 스캔: 대량 UPDATE/UPSERT", ln=True)
        pdf.ln(5)

        # 7-3. 개선 포인트 (AI Driven)
        pdf.set_font(font_name, "B", 11)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 8, "[ 개선 포인트 ]", ln=True)
        
        # points_match = re.search(r'\[개선 포인트\]\s*[:*]*\s*(.*?)(?=\n\s*[-*]|\[최종 진단\]|$)', insights, re.DOTALL)
        points_match = re.search(r'\[개선 포인트\]\s*[:*]*\s*(.*?)(?=\n\s*[*#]*\s*\[최종 진단\]|$)', insights, re.DOTALL)
        points_ai = points_match.group(1).strip() if points_match else "AI가 데이터 기반 최적화 방안을 분석 중입니다."
        
        pdf.set_font(font_name, "", 10)
        pdf.set_text_color(*TEXT_COL)
        pdf.multi_cell(190, 7, f"  {points_ai}", border=0)
        pdf.ln(5)

        # 7-4. 최종 총평
        pdf.set_font(font_name, "B", 11)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 8, "[ 시스템 최종 진단 및 총평 ]", ln=True)
        
        diag_text = ""
        opinion_match = re.search(r'\[최종 진단\]\s*[:*]*\s*(.*?)$', insights, re.DOTALL)
        if opinion_match:
            diag_text = opinion_match.group(1).strip()
        
        if not diag_text or "중입니다" in diag_text or "AI 연결 실패" in diag_text:
            diag_text = "AI 연결 안됨 (기술적 지표 기반의 수동 진단 필요)"
        
        pdf.set_font(font_name, "", 10)
        pdf.set_text_color(*TEXT_COL)
        pdf.multi_cell(190, 7, diag_text, border="L")
        
        # --- End of Report (Footer & Note) ---
        # Disable auto page break to prevent blank page at the very end
        pdf.set_auto_page_break(False)
        
        # Methodology Note (Moved up slightly)
        pdf.set_y(-25)
        pdf.set_font(font_name, "", 8)
        pdf.set_text_color(100, 100, 100)
        methodology_text = "※ HANA 공식 Plan Cache 지표를 기반으로 후보를 선정하며(SAP Help Portal 권고 준수), Peak Window는 SRE 표준 임계치를 적용하였습니다."
        pdf.multi_cell(190, 4, methodology_text, border=0, align='L')

        # Footer
        pdf.set_y(-15)
        pdf.set_font(font_name, "", 7)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 10, f"SAP Performance Analysis Report | {datetime.now().strftime('%Y-%m-%d')} | Page {pdf.page_no()}", align='R')
        
        # Restore auto page break just in case
        pdf.set_auto_page_break(True, 15)

        report_path = os.path.join(self.output_dir, output_filename)
        pdf.output(report_path)
        return report_path
