import google.generativeai as genai
import os
import re
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class SAPAIAnalyzer:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('gemini-3.1-flash-lite')
        else:
            logger.warning("Google API Key not found. AI insights will be generated in dummy mode.")
            self.model = None

    def analyze_performance(self, stats, windows, peak_sql, top_locks, top_cpu_label="N/A", top_lock_label="N/A"):
        """Generates professional analysis report using Gemini oriented for the 7-section structure."""
        windows_str = "\n".join([f"- {w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')} (Avg CPU: {w['avg_cpu']:.1f}%, Max CPU: {w['max_cpu']:.1f}%)" for w in windows])
        
        prompt = f"""
**[데이터 컨텍스트]**
- 날짜: {stats.get('date')}
- CPU: 평균 {stats.get('cpu_avg'):.1f}%, 최대 {stats.get('cpu_max')}%
- 메모리: 평균 {stats.get('mem_avg_pct'):.1f}%
- 피크 구간: {windows_str}
- 상위 SQL 요약: {peak_sql if peak_sql != 'None' else '정보 없음'}
- 상위 Lock 요약: {top_locks if top_locks != 'None' else '정보 없음'}

**[섹션별 작성 지침]**
아래 번호에 해당하는 내용을 상세히 작성해라. 리포트의 가독성을 위해 각 섹션은 명확히 구분해라.

1. Summary (요약): 
   전체적인 시스템 부하 수준과 리소스 안정성 요약.

5. SQL 영향도 분석 Commentary:
   상위 SQL들의 부하 패턴(Full Scan, 과다 호출 등)을 분석하고 비즈니스 영향 기술.

6. 서비스 대기(Lock Wait) 분석 Commentary:
   발생한 Lock의 성격(데이터 경합, 마스터 테이블 잠금 등)과 이로 인한 병목 현상 기술.

7. 종합 진단 및 기술적 제언:
   - [부하 원인 유형]: (배치성 / 동시성 / 집계형 중 선택 및 이유)
   - [개선 포인트]: (단기 조치 사항 및 중기 최적화 방안)
   - [최종 진단]: (시스템 전체 상태에 대한 최종 판정 및 운영 시사점)

[작성 스타일]
- 전문적인 SAP Basis Analyst의 관점에서 명확하고 간결하게 작성.
- '~음', '~함' 또는 하오체 등 전문적인 문체 사용.
"""
        
        if not self.model:
            return self._generate_dummy_insights(stats, windows, top_cpu_label, top_lock_label)
            
        try:
            response = self.model.generate_content(prompt)
            logger.info(f"AI Performance Analysis Response: {response.text}")
            return response.text
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            return self._generate_dummy_insights(stats, windows, top_cpu_label, top_lock_label)

    def generate_specific_actions(self, top_df):
        """Generates specific, row-by-row recommendations using Gemini for the top transactions."""
        if top_df is None or top_df.empty:
            return []
            
        if not self.model:
            return ["AI 연결 안됨"] * len(top_df)

        # Prepare a concise summary of each row for the prompt
        rows_info = []
        for i, (_, r) in enumerate(top_df.iterrows()):
            rows_info.append(f"{i+1}. [Prog:{r.get('PROGRAM_LABEL')}] [SQL:{r.get('SQL_LABEL')[:150]}] [Count:{r.get('EXEC_COUNT_peak')}] [Time:{r.get('AVG_EXEC_TIME_peak', 0):.2f}s] [Mem:{r.get('MAX_MEM_peak', 0)}]")

        items_str = "\n".join(rows_info)
        
        prompt = f"""
너는 시니어 SAP Basis / DB 전문가이다. 아래 상위 10개 부하 트랜잭션 목록에 대해 각각 **한 문장의 기술적 해결 방안**을 제시해라.
각 트랜잭션의 실행 횟수, 평균 시간, 메모리 사용량, SQL 패턴을 분석하여 SAP 표준 권고 사항이나 DB 튜닝 관점에서 실무적인 가이드를 제공해라.

**[분석 대상 리스트]**
{items_str}

**[작성 지침]**
1. 각 항목별로 **한 문장(최대 50자)**으로 간결하게 작성할 것.
2. 각 줄 앞에 반드시 '1.', '2.' 처럼 번호를 붙일 것.
3. 전문적이고 구체적인 용어(HINT, Buffer, Index, Bulk, Cache 등)를 사용할 것.
"""
        try:
            response = self.model.generate_content(prompt)
            logger.info(f"AI Specific Actions Response: {response.text}")
            lines = response.text.strip().split('\n')
            
            # Extract only the action part from numbered lines
            actions = []
            for line in lines:
                match = re.search(r'^\d+\.\s*(.*)$', line.strip())
                if match:
                    actions.append(match.group(1))
            
            # Match lengths
            if len(actions) < len(top_df):
                actions.extend(["AI 연결 실패"] * (len(top_df) - len(actions)))
            return actions[:len(top_df)]
            
        except Exception as e:
            logger.error(f"Gemini Specific Action failed: {e}")
            return ["AI 연결 실패"] * len(top_df)

    def _generate_dummy_insights(self, stats, windows, top_cpu_label, top_lock_label):
        return """
1. Summary (요약):
AI 연결 안됨 (Gemini API 할당량 초과 또는 연결 실패)

5. SQL 영향도 분석 Commentary:
AI 연결 안됨

6. 서비스 대기(Lock Wait) 분석 Commentary:
AI 연결 안됨

7. 종합 진단 및 기술적 제언:
- [부하 원인 유형]: AI 연결 실패
- [개선 포인트]: AI 연결 실패
- [최종 진단]: AI 연결 안됨
"""
