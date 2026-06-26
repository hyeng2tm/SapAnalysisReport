import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from fpdf import FPDF
import os
import logging
import re
import platform
import urllib.request
import tempfile
from datetime import datetime
from fpdf.fonts import FontFace

logger = logging.getLogger(__name__)

class SAPReporter:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.font_name = None

    def _split_list_items(self, text):
        normalized = str(text or "").replace("\r\n", "\n").strip()
        if not normalized:
            return []

        numbered_chunks = re.split(r'\n(?=\s*\d+\.\s+)', normalized)
        if len(numbered_chunks) == 1:
            numbered_chunks = re.split(r'\n(?=\s*[-*•]\s+)', normalized)

        items = []
        for chunk in numbered_chunks:
            cleaned = re.sub(r'^\s*(\d+\.|[-*•])\s*', '', chunk.strip())
            if cleaned:
                items.append(cleaned)

        return items or [normalized]

    def _build_operational_cause_rows(self, cause_text, top_sql, top_locks):
        normalized = str(cause_text or "")

        # 배치성 부하: AI 텍스트 OR 실행건수 10,000회 이상인 SQL이 상위에 존재하면 '해당'
        is_batch_ai = "배치" in normalized
        is_batch_data = False
        if top_sql is not None and not top_sql.empty and 'EXEC_COUNT_peak' in top_sql.columns:
            is_batch_data = bool((top_sql['EXEC_COUNT_peak'] >= 10000).any())
        batch_status = "해당" if (is_batch_ai or is_batch_data) else "점검 필요"
        batch_basis = "피크 시간대에 반복 실행되는 배치성 SQL 및 대량 처리 작업 여부 확인 필요"
        if is_batch_data and top_sql is not None and not top_sql.empty:
            top_batch = top_sql.sort_values('EXEC_COUNT_peak', ascending=False).iloc[0]
            prog = str(top_batch.get('PROGRAM_LABEL', ''))[:30]
            cnt  = int(top_batch.get('EXEC_COUNT_peak', 0))
            batch_basis = f"{prog} 등 {cnt:,}회 반복 실행 확인 — 배치/반복 호출성 SQL 집중 점검 필요"

        # 동시성 및 Lock: AI 텍스트 OR top_locks 데이터 존재
        is_lock_ai   = "동시성" in normalized or "락" in normalized
        is_lock_data = top_locks is not None and not top_locks.empty
        lock_status  = "해당" if (is_lock_ai or is_lock_data) else "낮음"
        lock_basis   = "Lock Wait 상위 항목 및 FOR UPDATE/UPSERT/채번 구문 사용 여부 기준"
        if is_lock_data:
            top_lock_row = top_locks.sort_values('TOTAL_LOCK_WAIT_SEC_peak', ascending=False).iloc[0]
            lock_prog = str(top_lock_row.get('PROGRAM_LABEL', ''))[:30]
            lock_sec  = float(top_lock_row.get('TOTAL_LOCK_WAIT_SEC_peak', 0))
            lock_basis = f"{lock_prog} Lock Wait {lock_sec:,.1f}sec 확인 — 채번·트랜잭션 직렬화 점검 필요"

        # 대량 집계 및 스캔: AI 텍스트 OR CAUSE 컬럼에 '집계'/'스캔' 포함 OR 총 메모리 ≥ 1GB SQL 존재
        is_scan_ai   = "집계" in normalized or "스캔" in normalized
        is_scan_data = False
        if top_sql is not None and not top_sql.empty:
            if 'CAUSE' in top_sql.columns:
                is_scan_data = top_sql['CAUSE'].astype(str).str.contains('집계|스캔').any()
            if not is_scan_data and 'TOTAL_MEM_peak' in top_sql.columns:
                is_scan_data = bool((top_sql['TOTAL_MEM_peak'] >= 1024**3).any())
        scan_status = "해당" if (is_scan_ai or is_scan_data) else "점검 필요"
        scan_basis  = "집계성 조회, 대량 읽기/쓰기, 메모리 사용량이 큰 SQL 존재 여부 기준"
        if is_scan_data and top_sql is not None and not top_sql.empty:
            top_mem = top_sql.sort_values('TOTAL_MEM_peak', ascending=False).iloc[0]
            mem_prog = str(top_mem.get('PROGRAM_LABEL', ''))[:30]
            mem_gb   = float(top_mem.get('TOTAL_MEM_peak', 0)) / (1024**3)
            scan_basis = f"{mem_prog} 총 {mem_gb:,.1f}GB 메모리 사용 — 풀스캔·집계 플랜 최적화 필요"

        rows = [
            {"category": "배치성 부하",     "status": batch_status, "basis": batch_basis},
            {"category": "동시성 및 Lock",  "status": lock_status,  "basis": lock_basis},
            {"category": "대량 집계 및 스캔", "status": scan_status,  "basis": scan_basis},
        ]
        return rows

    def _build_operational_improvement_rows(self, stats, windows, top_sql, top_locks, global_top, points_text):
        parsed_items = self._split_list_items(points_text)
        rows = []
        for index, item in enumerate(parsed_items, start=1):
            if "Lock" in item or "락" in item or "UPSERT" in item or "FOR UPDATE" in item:
                category = "동시성 완화"
                effect = "락 대기시간 축소 및 응답 지연 완화"
            elif "배치" in item or "시간대" in item:
                category = "운영 스케줄 조정"
                effect = "피크 시간대 부하 분산"
            elif "메모리" in item:
                category = "메모리 관리"
                effect = "메모리 급증 및 캐시 압박 완화"
            else:
                category = "SQL 성능 개선"
                effect = "실행시간 단축 및 CPU 사용률 안정화"

            rows.append(
                {
                    "priority": f"{index}",
                    "category": category,
                    "action": item,
                    "effect": effect,
                }
            )

        if rows:
            return rows

        source_df = global_top if global_top is not None and not global_top.empty else top_sql
        if source_df is not None and not source_df.empty:
            lead_program = str(source_df.iloc[0].get('PROGRAM_LABEL', '주요 프로그램'))
            rows.append(
                {
                    "priority": "1",
                    "category": "SQL 성능 개선",
                    "action": f"{lead_program} 관련 상위 SQL의 실행계획, 인덱스, 호출 구조를 우선 점검",
                    "effect": "핵심 부하 SQL의 평균 및 최대 응답시간 단축",
                }
            )

        if top_locks is not None and not top_locks.empty:
            rows.append(
                {
                    "priority": str(len(rows) + 1),
                    "category": "동시성 완화",
                    "action": "Lock 경합 구간의 채번, FOR UPDATE, UPSERT 접근 순서 및 트랜잭션 범위를 재검토",
                    "effect": "Lock Wait 감소 및 동시 처리 안정성 확보",
                }
            )

        peak_windows = self._format_peak_windows(windows)
        rows.append(
            {
                "priority": str(len(rows) + 1),
                "category": "운영 스케줄 조정",
                "action": f"피크 시간대({peak_windows})에 집중되는 배치 및 집계 작업의 실행 시점을 분산",
                "effect": "CPU 피크 완화 및 사용자 체감 성능 개선",
            }
        )

        if float(stats.get('mem_avg_pct', 0) or 0) >= 80:
            rows.append(
                {
                    "priority": str(len(rows) + 1),
                    "category": "메모리 관리",
                    "action": "고메모리 SQL과 캐시 사용량을 점검하고 메모리 상한 정책을 재확인",
                    "effect": "메모리 병목과 연쇄 성능 저하 예방",
                }
            )

        return rows

    def _build_operational_diagnosis_rows(self, stats, windows, top_sql, top_locks, diagnosis_text):
        # Use globally highest PRIORITY program, not time-ordered first row.
        if top_sql is not None and not top_sql.empty:
            source_program = str(top_sql.sort_values('PRIORITY', ascending=False).iloc[0].get('PROGRAM_LABEL', 'N/A'))
        else:
            source_program = 'N/A'
        if top_locks is not None and not top_locks.empty:
            lock_program = str(top_locks.sort_values('TOTAL_LOCK_WAIT_SEC_peak', ascending=False).iloc[0].get('PROGRAM_LABEL', 'N/A'))
        else:
            lock_program = 'N/A'
        return [
            {"item": "종합 판단", "detail": diagnosis_text},
            {"item": "주요 피크 시간대", "detail": self._format_peak_windows(windows)},
            {"item": "핵심 부하 프로그램", "detail": source_program},
            {"item": "주요 Lock 경합 프로그램", "detail": lock_program},
            {"item": "운영 권고", "detail": "단기적으로는 상위 SQL 및 Lock 경합 조치, 중기적으로는 배치 분산과 구조 개선을 병행"},
        ]

    def _collapse_sql_report_rows(self, top_sql):
        if top_sql is None or top_sql.empty:
            return top_sql

        collapsed = top_sql.copy()
        collapsed['PEAK_PERIOD'] = collapsed['PEAK_PERIOD'].astype(str)

        def join_periods(values):
            unique_values = []
            for value in values:
                if value not in unique_values:
                    unique_values.append(value)
            return ", ".join(unique_values)

        grouped = (
            collapsed
            .groupby(['PROGRAM_LABEL', 'SQL_LABEL', 'CAUSE'], dropna=False, as_index=False)
            .agg({
                'PEAK_PERIOD': join_periods,
                'EXEC_COUNT_peak': 'sum',
                'TOTAL_EXEC_TIME_peak': 'sum',
                'TOTAL_MEM_peak': 'sum',
                'MAX_EXEC_TIME_peak': 'max',
                'MAX_MEM_peak': 'max',
                'PRIORITY': 'max',
            })
        )

        grouped['AVG_EXEC_TIME_peak'] = grouped['TOTAL_EXEC_TIME_peak'] / grouped['EXEC_COUNT_peak'].replace(0, 1)
        grouped['AVG_MEM_peak'] = grouped['TOTAL_MEM_peak'] / grouped['EXEC_COUNT_peak'].replace(0, 1)

        return grouped.sort_values(['PRIORITY', 'TOTAL_EXEC_TIME_peak'], ascending=[False, False]).reset_index(drop=True)

    def _extract_sql_commentary(self, insights):
        sql_comm_match = re.search(r'[#]*\s*5\.\s*(?:부하 시간대 영향 SQL 및 프로그램 분석|SQL 영향도 분석|Peak Load 구간별 SQL 영향도 분석)\s*(?:\([^)]*\))?\s*[:*]*\s*(.*?)(?=\n\s*[#]*\s*6\.|$)', insights, re.DOTALL)
        if not sql_comm_match:
            sql_comm_match = re.search(r'5\.\s*SQL 영향도 분석 Commentary\s*[:*]*\s*(.*?)(?=\n\s*[*#-]*\s*6\.|\[|$)', insights, re.DOTALL)

        sql_comm = sql_comm_match.group(1).strip() if sql_comm_match else ""
        if not sql_comm:
            return "상위 SQL 실행 통계에 기반하여 AI가 부하 패턴과 운영 영향도를 분석 중입니다."

        # If the AI structured section 5 into 5-1/5-2 subsections, drop 5-1 because the table already covers it.
        subsection_match = re.search(r'(?:#+\s*)?5-2[\.)\s:].*', sql_comm, re.DOTALL)
        if subsection_match:
            sql_comm = subsection_match.group(0).strip()

        # Remove repeated 5-1 subsection blocks when only part of the section is needed.
        sql_comm = re.sub(
            r'(?:#+\s*)?5-1[\.)\s:].*?(?=(?:\n\s*(?:#+\s*)?5-2[\.)\s:])|$)',
            '',
            sql_comm,
            flags=re.DOTALL,
        ).strip()

        sql_comm = re.sub(r'^(?:#+\s*)?5-2[\.)\s:]*', '', sql_comm).strip()
        sql_comm = re.sub(r'\n\s*---+\s*$', '', sql_comm).strip()
        return sql_comm or "상위 SQL 실행 통계에 기반하여 AI가 부하 패턴과 운영 영향도를 분석 중입니다."

    def _extract_lock_commentary(self, insights):
        lock_comm_match = re.search(r'[#]*\s*6\.\s*(?:서비스 대기\(Lock Wait\) 분석|Lock Wait 분석|서비스 대기 분석)\s*(?:\([^)]*\))?\s*[:*]*\s*(.*?)(?=\n\s*[#]*\s*7\.|$)', insights, re.DOTALL)
        if not lock_comm_match:
            lock_comm_match = re.search(r'6\.\s*서비스 대기\(Lock Wait\) 분석 Commentary\s*[:*]*\s*(.*?)(?=\n\s*[*#-]*\s*7\.|\[|$)', insights, re.DOTALL)

        lock_comm = lock_comm_match.group(1).strip() if lock_comm_match else ""
        if not lock_comm:
            return "탐지된 Lock 경합 데이터에 기반하여 AI가 병목 원인을 분석 중입니다."

        # Drop markdown table/listing blocks because section 6 already renders structured lock rows.
        cleaned_lines = []
        for line in lock_comm.splitlines():
            stripped = line.strip()
            if stripped.startswith("|"):
                continue
            if stripped.startswith("### Lock 분석 결과"):
                continue
            if "Lock Wait 상세 파일 확보 시" in stripped:
                continue
            cleaned_lines.append(line)

        lock_comm = "\n".join(cleaned_lines)
        lock_comm = re.sub(r'\n\s*---+\s*$', '', lock_comm).strip()
        lock_comm = re.sub(r'\n{3,}', '\n\n', lock_comm)
        return lock_comm or "탐지된 Lock 경합 데이터에 기반하여 AI가 병목 원인을 분석 중입니다."

    def _is_placeholder_text(self, text):
        if not text:
            return True
        normalized = str(text).strip()
        placeholder_markers = [
            "AI 연결 안됨",
            "분석 중입니다",
            "분석 중",
            "데이터 기반 최적화 방안을 분석 중입니다",
            "기술적 지표 기반의 수동 진단 필요",
        ]
        return any(marker in normalized for marker in placeholder_markers)

    def _format_peak_windows(self, windows):
        if not windows:
            return "주요 피크 시간대"
        return ", ".join(
            f"{window['start'].strftime('%H:%M')}~{window['end'].strftime('%H:%M')}"
            for window in windows
        )

    def _build_default_improvement_points(self, stats, windows, top_sql, top_locks, global_top):
        points = []
        peak_windows = self._format_peak_windows(windows)

        source_df = global_top if global_top is not None and not global_top.empty else top_sql
        if source_df is not None and not source_df.empty:
            lead_row = source_df.iloc[0]
            program_label = str(lead_row.get('PROGRAM_LABEL', '주요 프로그램'))
            avg_exec = float(lead_row.get('AVG_EXEC_TIME_peak', 0) or 0)
            max_exec = float(lead_row.get('MAX_EXEC_TIME_peak', 0) or 0)
            points.append(
                f"1. {program_label} 중심의 상위 SQL에 대해 실행계획과 인덱스를 우선 점검하고, 평균 {avg_exec:.2f}s / 최대 {max_exec:.1f}s 구간의 지연 원인을 제거할 필요가 있음."
            )

        if top_locks is not None and not top_locks.empty:
            lock_row = top_locks.iloc[0]
            lock_program = str(lock_row.get('PROGRAM_LABEL', '동시성 관련 프로그램'))
            lock_wait = float(lock_row.get('TOTAL_LOCK_WAIT_SEC_peak', 0) or 0)
            lock_mode = str(top_locks.attrs.get('selection_mode', 'strict')) if hasattr(top_locks, 'attrs') else 'strict'
            mode_text = "완화 기준으로도" if lock_mode == "relaxed" else "엄격 기준에서"
            points.append(
                f"2. {lock_program} 구간에서 Lock Wait {lock_wait:.1f}s가 확인되므로 {mode_text} 식별된 동시성 경합을 줄이기 위해 FOR UPDATE, UPSERT, 채번 오브젝트 접근 순서를 재검토해야 함."
            )

        if stats.get('high_load_count', 0) > 0:
            points.append(
                f"3. CPU 고부하가 관측된 {peak_windows} 시간대에는 배치 실행 시점을 분산하고, 동일 시간대의 대량 집계 또는 인터페이스 작업을 분리하는 운영 조정이 필요함."
            )

        mem_avg = float(stats.get('mem_avg_pct', 0) or 0)
        if mem_avg >= 80:
            points.append(
                f"4. 평균 메모리 사용률이 {mem_avg:.1f}% 수준이므로 대용량 작업의 메모리 상한과 결과 캐시 사용량을 함께 점검하는 것이 바람직함."
            )

        if not points:
            points.append("1. 수집된 성능 지표를 기준으로 상위 SQL과 배치 시간대를 우선 추적하고, 반복적으로 재현되는 피크 구간의 실행계획을 점검할 필요가 있음.")

        return "\n".join(points)

    def _build_default_final_diagnosis(self, stats, windows, top_sql, top_locks):
        cpu_max = float(stats.get('cpu_max', 0) or 0)
        cpu_p95 = float(stats.get('cpu_p95', 0) or 0)
        mem_avg = float(stats.get('mem_avg_pct', 0) or 0)
        high_load_count = int(stats.get('high_load_count', 0) or 0)
        peak_windows = self._format_peak_windows(windows)

        if cpu_max >= 90 or high_load_count >= 3:
            severity = "높은 수준의 성능 부담"
        elif cpu_p95 >= 80:
            severity = "주의가 필요한 성능 부담"
        else:
            severity = "국지적인 성능 부담"

        causes = []
        if top_sql is not None and not top_sql.empty:
            causes.append(f"상위 SQL/프로그램({str(top_sql.iloc[0].get('PROGRAM_LABEL', 'N/A'))}) 중심의 처리 집중")
        if top_locks is not None and not top_locks.empty:
            causes.append(f"Lock 경합({str(top_locks.iloc[0].get('PROGRAM_LABEL', 'N/A'))})")
        cause_text = ", ".join(causes) if causes else "피크 시간대의 처리 집중"

        mem_text = "메모리 측면의 추가 점검도 필요함" if mem_avg >= 80 else "메모리는 비교적 안정적으로 관리되고 있음"

        return (
            f"이마트 SAP 시스템은 {peak_windows} 구간을 중심으로 {severity}이 관찰되었으며, 주요 원인은 {cause_text}으로 판단됨. "
            f"당일 최대 CPU는 {cpu_max:.1f}%, p95는 {cpu_p95:.1f}%, 고부하 샘플은 {high_load_count}회로 확인되었고, {mem_text}. "
            f"단기적으로는 상위 SQL 튜닝과 동시성 완화 조치를 우선 적용하고, 중기적으로는 배치 분산 및 작업 구조 개선을 병행하는 것이 타당함."
        )

    def get_unicode_font_path(self):
        """
        Attempt to find or download a Unicode font that supports CJK characters.
        Returns the font path and font name to use in FPDF.
        CJK fonts are prioritized over other Unicode fonts.
        """
        # First priority: Pretendard (preferred report font)
        pretendard_candidates = [
            os.path.join(self.output_dir, "fonts", "Pretendard-Regular.ttf"),
            os.path.join(self.output_dir, "fonts", "PretendardVariable.ttf"),
            os.path.join("report", "fonts", "Pretendard-Regular.ttf"),
            os.path.join("report", "fonts", "PretendardVariable.ttf"),
            r"C:\Windows\Fonts\Pretendard-Regular.ttf",
            r"C:\Windows\Fonts\PretendardVariable.ttf",
            "/Library/Fonts/Pretendard-Regular.ttf",
            "/Library/Fonts/PretendardVariable.ttf",
            "/usr/share/fonts/truetype/pretendard/Pretendard-Regular.ttf",
            "/usr/share/fonts/truetype/pretendard/PretendardVariable.ttf",
        ]

        for font_path in pretendard_candidates:
            if os.path.exists(font_path):
                logger.info(f"Using Pretendard font at {font_path}")
                return font_path, "Pretendard"

        # First priority: Arial Unicode MS (known to have full Korean support)
        arial_unicode_paths = [
            r"C:\Windows\Fonts\Arial-Unicode.ttf",
            r"C:\Windows\Fonts\arialuni.ttf",
            r"C:\Windows\Fonts\ARIALUN.TTF",
            "/System/Library/Fonts/Arial-Unicode.ttf",
            "/Library/Fonts/Arial-Unicode.ttf",
            "/usr/share/fonts/truetype/msttcorefonts/Arial-Unicode.ttf",
        ]
        
        for font_path in arial_unicode_paths:
            if os.path.exists(font_path):
                logger.info(f"Found Arial Unicode MS (full Korean support) at {font_path}")
                return font_path, "ArialUnicode"
        
        # Second priority: Try to download Noto Sans CJK (guaranteed Korean support)
        logger.info("Arial Unicode MS not found locally. Downloading Noto Sans CJK for full Korean support...")
        font_path, font_label = self._try_download_noto_sans_cjk()
        if font_path:
            return font_path, font_label
        
        # Third priority: Other CJK-labeled fonts (may have limited Korean support)
        other_cjk_fonts = [
            (r"C:\Windows\Fonts\NotoSansKR-VF.ttf", "NotoSansKR"),
            (r"C:\Windows\Fonts\msyh.ttc", "MicrosoftYaHei"),  # May not have Korean
            ("/usr/share/fonts/opentype/noto/NotoSansKR-VF.ttf", "NotoSansKR"),
        ]
        
        for font_path, font_label in other_cjk_fonts:
            if os.path.exists(font_path):
                logger.warning(f"Using fallback font {font_label} (may have limited Korean support)")
                return font_path, font_label
        
        # Final fallback: Any Unicode font
        final_fallback_fonts = [
            (r"C:\Windows\Fonts\DejaVuSans.ttf", "DejaVuSans"),
            (r"C:\Windows\Fonts\Arial.ttf", "Arial"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans"),
            ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "LiberationSans"),
        ]
        
        for font_path, font_label in final_fallback_fonts:
            if os.path.exists(font_path):
                logger.warning(f"Using fallback font {font_label} (will NOT support Korean characters)")
                return font_path, font_label
        
        return None, None
    
    def _try_download_noto_sans_cjk(self):
        """
        Attempt to download and cache Noto Sans CJK font.
        Returns tuple of (font_path, font_label) or (None, None) if download fails.
        """
        try:
            font_cache_dir = os.path.expanduser("~/.sap_report_fonts")
            os.makedirs(font_cache_dir, exist_ok=True)
            
            font_path = os.path.join(font_cache_dir, "NotoSansCJK-Regular.ttc")
            
            # If already cached, use it
            if os.path.exists(font_path):
                logger.info(f"Using cached Noto Sans CJK font from {font_path}")
                return font_path, "NotoSansCJK"
            
            # Try downloading from different sources
            urls = [
                "https://github.com/google/fonts/raw/main/ofl/notosanscjk/NotoSansCJK-Regular.ttc",
                "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/Variable/NotoSansCJK-VF.ttf",
            ]
            
            for url in urls:
                try:
                    logger.info(f"Attempting to download from: {url}")
                    urllib.request.urlretrieve(url, font_path, reporthook=self._download_progress)
                    logger.info(f"Successfully downloaded Noto Sans CJK to {font_path}")
                    return font_path, "NotoSansCJK"
                except Exception as e:
                    logger.debug(f"Failed to download from {url}: {e}")
                    if os.path.exists(font_path):
                        os.remove(font_path)
                    continue
            
            logger.warning("All download attempts failed")
            return None, None
                
        except Exception as e:
            logger.warning(f"Error attempting to download font: {e}")
            return None, None
    
    def _download_progress(self, block_num, block_size, total_size):
        """Simple progress indicator for file download"""
        if total_size > 0:
            percent = min(100, (block_num * block_size * 100) // total_size)
            if percent % 10 == 0 and percent > 0:
                logger.debug(f"Download progress: {percent}%")

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
        
        # Font Configuration - Use new method
        font_name = "Helvetica"
        
        font_path, font_label = self.get_unicode_font_path()
        
        if font_path and font_label:
            try:
                pdf.add_font(font_label, "", font_path, uni=True)
                # Try to add bold variant if possible (may fail for some fonts)
                try:
                    pdf.add_font(font_label, "B", font_path, uni=True)
                except:
                    logger.debug(f"Could not add bold variant for {font_label}")
                font_name = font_label
                logger.info(f"Successfully loaded Unicode font: {font_name}")
            except Exception as e:
                logger.warning(f"Failed to add font to PDF: {e}. Falling back to Helvetica.")
                font_name = "Helvetica"
        else:
            logger.warning("No Unicode font available. PDF may have rendering issues with non-ASCII characters.")

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
        # Handle potential encoding issues with Korean characters
        try:
            pdf.cell(0, 20, report_title, ln=True, align='C')
        except Exception as e:
            logger.warning(f"Failed to render title with font {font_name}: {e}. Using fallback title.")
            pdf.cell(0, 20, f"{stats.get('date', 'N/A')} SAP DB Server Monitoring Report", ln=True, align='C')
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
        
        logger.info(f"\n{'='*80}")
        logger.info("[PDF REPORT: AI INSIGHTS PROCESSING]")
        logger.info(f"  insights type: {type(insights)}")
        logger.info(f"  insights length: {len(insights) if isinstance(insights, str) else 'N/A'}")
        logger.info(f"  insights (first 500 chars): {str(insights)[:500]}")
        logger.info(f"{'='*80}\n")
        
        # Try to match both '1. Summary' and '## 2. Summary' formats
        summary_match = re.search(r'[#]*\s*[0-9]+\.\s*(?:Summary|요약)\s*(?:\([^)]*\))?\s*[:*]*\s*(.*?)(?=\n\s*[#]*\s*\d+\.|$)', insights, re.DOTALL)
        # Fallback to old format if no match
        if not summary_match:
            summary_match = re.search(r'1\.\s*Summary\s*[:*]*\s*(.*?)(?=\n\s*[*#]*\s*\d\.|\[|$)', insights, re.DOTALL)
        summary_text = summary_match.group(1).strip() if summary_match else "AI 연결 안됨 (데이터 기반 요약 기능 비활성화)"
        
        logger.info(f"[SUMMARY EXTRACTION]")
        logger.info(f"  match found: {summary_match is not None}")
        if summary_match:
            logger.info(f"  extracted (first 300 chars): {summary_match.group(1).strip()[:300]}")
        logger.info(f"  final text (first 300 chars): {summary_text[:300]}\n")
        
        # More robust removal: if the first line contains 'Summary' or '요약', remove that whole line.
        summary_lines = summary_text.split('\n')
        if summary_lines and ('Summary' in summary_lines[0] or '요약' in summary_lines[0]):
            summary_text = '\n'.join(summary_lines[1:]).strip()
            
        summary_text = re.sub(r'\n\s*---+\s*$', '', summary_text).strip()
        pdf.multi_cell(190, 7, summary_text, border=0)
        pdf.ln(4)

        # --- 2. CPU/메모리 요약 테이블 ---
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
            # Try to match both '3. 차트 해석' and '## 3. 차트 해석' formats
            chart_match = re.search(r'[#]*\s*[0-9]+\.\s*차트\s*해석\s*[:*]*\s*(.*?)(?=\n\s*[#]*\s*\d+\.|$)', insights, re.DOTALL)
            # Fallback to old format
            if not chart_match:
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

        # --- 5. 부하 시간대 영향 SQL 및 프로그램 분석 ---
        ensure_space(60)
        pdf.set_font(font_name, "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 10, "5. 부하 시간대 영향 SQL 및 프로그램 분석", ln=True)
        
        pdf.set_font(font_name, "", 9)
        pdf.set_text_color(*TEXT_COL)
        sql_desc = "※ Peak Window별로 영향 SQL을 우선순위(PRIORITY) 기준으로 정렬해 표시하였으며, 선별 조건은 실행시간/메모리 복합 기준을 적용합니다."
        pdf.multi_cell(190, 6, sql_desc, ln=True)
        pdf.ln(2)
        
        if top_sql is not None and not top_sql.empty:
            report_top_sql = top_sql.copy()
            report_top_sql['PEAK_PERIOD'] = report_top_sql['PEAK_PERIOD'].astype(str)
            report_top_sql['PRIORITY'] = report_top_sql.get('PRIORITY', 0).fillna(0).astype(float)
            report_top_sql['TOTAL_EXEC_TIME_peak'] = report_top_sql.get('TOTAL_EXEC_TIME_peak', 0).fillna(0).astype(float)

            peak_order = []
            for peak in report_top_sql['PEAK_PERIOD'].tolist():
                if peak not in peak_order:
                    peak_order.append(peak)
            peak_rank_map = {name: idx for idx, name in enumerate(peak_order)}
            report_top_sql['__peak_rank'] = report_top_sql['PEAK_PERIOD'].map(lambda v: peak_rank_map.get(v, 10**6))
            report_top_sql = report_top_sql.sort_values(
                ['__peak_rank', 'PRIORITY', 'TOTAL_EXEC_TIME_peak'],
                ascending=[True, False, False]
            ).reset_index(drop=True)
            report_top_sql['WINDOW_RANK'] = report_top_sql.groupby('PEAK_PERIOD').cumcount() + 1

            pdf.set_font(font_name, "", 7)
            # Column Order: 시간구간, 우선, 프로그램, 쿼리, 횟수, 실행시간(s), 메모리, 설명
            with pdf.table(col_widths=(20, 10, 22, 40, 13, 22, 23, 40), line_height=5.0,
                          headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
                h = table.row()
                for header in ["시간구간", "우선", "프로그램", "대표 쿼리", "횟수", "실행시간", "메모리", "설명"]:
                    h.cell(header)
                last_period = None
                for _, r in report_top_sql.iterrows():
                    curr_period = str(r.get('PEAK_PERIOD', 'N/A'))
                    display_period = "" if curr_period == last_period else curr_period
                    last_period = curr_period

                    row = table.row()
                    row.cell(display_period)
                    row.cell(f"{int(r.get('WINDOW_RANK', 0))}")
                    row.cell(str(r.get('PROGRAM_LABEL', 'N/A'))[:25])
                    row.cell(str(r.get('SQL_LABEL', 'N/A'))[:100])
                    row.cell(f"{int(r.get('EXEC_COUNT_peak', 0)):,}")
                    time_str = f"총 {r.get('TOTAL_EXEC_TIME_peak', 0):,.1f}s\n평균 {r.get('AVG_EXEC_TIME_peak', 0):,.1f}s\n최대 {r.get('MAX_EXEC_TIME_peak', 0):,.1f}s"
                    row.cell(time_str)
                    mem_str = f"총 {self.format_bytes(r.get('TOTAL_MEM_peak', 0))}\n평균 {self.format_bytes(r.get('AVG_MEM_peak', 0))}\n최대 {self.format_bytes(r.get('MAX_MEM_peak', 0))}"
                    row.cell(mem_str)
                    row.cell(f"점수 {r.get('PRIORITY', 0):.4f}\n{str(r.get('CAUSE', '-'))}")

            if '__peak_rank' in report_top_sql.columns:
                report_top_sql = report_top_sql.drop(columns=['__peak_rank'])
            pdf.ln(2)
            # AI SQL Commentary
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(*TEXT_COL)
            sql_comm = self._extract_sql_commentary(insights)
            pdf.multi_cell(190, 6, sql_comm, border="L")
        pdf.ln(8)

        # --- 6. 서비스 대기(Lock Wait) 분석 ---
        ensure_space(60)
        pdf.set_font(font_name, "B", 14)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 10, "6. 서비스 대기(Lock Wait) 분석", ln=True)
        
        pdf.set_font(font_name, "", 9)
        pdf.set_text_color(*TEXT_COL)
        ratio_thr = 0.3
        quantile_thr = 0.9
        top_per_peak = 3
        lock_selection_mode = "strict"
        if top_locks is not None and hasattr(top_locks, 'attrs'):
            ratio_thr = float(top_locks.attrs.get('ratio_threshold', ratio_thr))
            quantile_thr = float(top_locks.attrs.get('quantile_threshold', quantile_thr))
            top_per_peak = int(top_locks.attrs.get('top_per_peak', top_per_peak))
            lock_selection_mode = str(top_locks.attrs.get('selection_mode', lock_selection_mode))

        quantile_pct = int(round(quantile_thr * 100))
        lock_desc = f"※ 기본 기준: Lock Wait Ratio {ratio_thr:.2f} 이상 + 누적 Lock 대기시간 상위 {100 - quantile_pct}% + 피크별 상위 {top_per_peak}건"
        pdf.multi_cell(190, 6, lock_desc, ln=True)
        if lock_selection_mode == "relaxed":
            pdf.multi_cell(190, 6, "※ 적용 기준: 엄격 기준 충족 항목이 없어 완화 기준(양수 Lock 대기시간 상위 항목)으로 출력하였습니다.", ln=True)
        elif lock_selection_mode == "strict":
            pdf.multi_cell(190, 6, "※ 적용 기준: 엄격 기준 충족 항목만 반영되었습니다.", ln=True)
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
                    row.cell(self.format_bytes(r.get('TOTAL_MEM_peak', 0)))
                    row.cell(str(r.get('CAUSE', '-')))
            pdf.ln(2)
            # AI Lock Commentary
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(*TEXT_COL)
            lock_comm = self._extract_lock_commentary(insights)
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
        
        cause_type_match = re.search(r'\[부하 원인 유형\]\s*[:*]*\s*(.*?)(?=\n\s*[*#]*\s*\[개선 포인트\]|\n\s*[*#]*\s*\[최종 진단\]|$)', insights, re.DOTALL)
        cause_type_ai = cause_type_match.group(1).strip() if cause_type_match else ""

        cause_rows = self._build_operational_cause_rows(cause_type_ai, top_sql, top_locks)
        pdf.set_font(font_name, "", 8)
        with pdf.table(col_widths=(40, 25, 125), line_height=6.0,
                      headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
            h = table.row()
            for header in ["원인 유형", "판정", "운영 판단 근거"]:
                h.cell(header)
            for item in cause_rows:
                row = table.row()
                row.cell(item['category'])
                row.cell(item['status'])
                row.cell(item['basis'])
        pdf.ln(5)

        # 7-3. 개선 포인트 (AI Driven)
        pdf.set_font(font_name, "B", 11)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 8, "[ 개선 포인트 ]", ln=True)
        
        # points_match = re.search(r'\[개선 포인트\]\s*[:*]*\s*(.*?)(?=\n\s*[-*]|\[최종 진단\]|$)', insights, re.DOTALL)
        points_match = re.search(r'(?:\[\s*개선 포인트\s*\]|개선 포인트)\s*[:*]*\s*(.*?)(?=\n\s*[*#]*\s*(?:\[\s*최종 진단\s*\]|최종 진단)|$)', insights, re.DOTALL)
        points_ai = points_match.group(1).strip() if points_match else ""
        if self._is_placeholder_text(points_ai):
            points_ai = self._build_default_improvement_points(stats, windows, top_sql, top_locks, global_top)

        improvement_rows = self._build_operational_improvement_rows(stats, windows, top_sql, top_locks, global_top, points_ai)
        pdf.set_font(font_name, "", 8)
        with pdf.table(col_widths=(12, 32, 96, 50), line_height=6.0,
                      headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
            h = table.row()
            for header in ["우선", "개선 영역", "권고 조치", "기대 효과"]:
                h.cell(header)
            for item in improvement_rows:
                row = table.row()
                row.cell(item['priority'])
                row.cell(item['category'])
                row.cell(item['action'])
                row.cell(item['effect'])
        pdf.ln(5)

        # 7-4. 최종 총평
        pdf.set_font(font_name, "B", 11)
        pdf.set_text_color(*NAVY)
        pdf.cell(0, 8, "[ 시스템 최종 진단 및 총평 ]", ln=True)
        
        diag_text = ""
        opinion_match = re.search(r'(?:\[\s*최종 진단\s*\]|최종 진단)\s*[:*]*\s*(.*?)$', insights, re.DOTALL)
        if opinion_match:
            diag_text = opinion_match.group(1).strip()
        
        if self._is_placeholder_text(diag_text):
            diag_text = self._build_default_final_diagnosis(stats, windows, top_sql, top_locks)

        diagnosis_rows = self._build_operational_diagnosis_rows(stats, windows, top_sql, top_locks, diag_text)
        pdf.set_font(font_name, "", 8)
        with pdf.table(col_widths=(42, 148), line_height=6.0,
                      headings_style=FontFace(fill_color=NAVY, color=(255, 255, 255))) as table:
            h = table.row()
            for header in ["진단 항목", "내용"]:
                h.cell(header)
            for item in diagnosis_rows:
                row = table.row()
                row.cell(item['item'])
                row.cell(item['detail'])
        
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
