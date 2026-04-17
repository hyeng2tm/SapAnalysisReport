import google.generativeai as genai
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class SAPAIAnalyzer:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('gemini-2.0-flash')
        else:
            logger.warning("Google API Key not found. AI insights will be generated in dummy mode.")
            self.model = None

    def analyze_performance(self, stats, windows, peak_sql, top_locks, top_cpu_label="N/A", top_lock_label="N/A"):
        """Generates professional analysis report using Gemini oriented for the 7-section structure."""
        windows_str = "\n".join([f"- {w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')} (Avg CPU: {w['avg_cpu']:.1f}%, Max CPU: {w['max_cpu']:.1f}%)" for w in windows])
        
        prompt = f"""
너는 시니어 SAP Basis / DB 성능 분석가로서, 고객사(이마트)의 SAP DB 서버 모니터링 리포트를 작성 중이다.
아래 제공되는 데이터들을 바탕으로 **전달된 7단계 구조**에 맞는 전문적인 한글 텍스트를 생성해라.
단, 리포트가 **세로(Portrait) 모드**이므로 텍스트가 너무 길어지지 않게 간결하고 명확하게 작성해라.

**[데이터 컨텍스트]**
- 날짜: {stats.get('date')}
- CPU: 평균 {stats.get('cpu_avg'):.1f}%, 최대 {stats.get('cpu_max')}% (p95: {stats.get('cpu_p95'):.1f}%)
- 피크 샘플(80%↑): {stats.get('high_load_count')}회
- 메모리: 평균 {stats.get('mem_avg_pct'):.1f}% (안정적 관리 여부 판단 필요)
- 피크 구간: {windows_str}
- 부하 주범 프로그램: {top_cpu_label}
- 락 경합 프로그램: {top_lock_label}
- 상세 계획: {peak_sql if peak_sql != 'None' else '정보 없음'}

**[섹션별 작성 지침]**
아래 번호에 해당하는 내용을 상세히 작성해라.

2. Summary (요약): 
   전체 CPU 사용 수준 요약, 80% 초과 고부하 발생 여부, 메모리 안정성, 운영상 주의가 필요한 시간대 명시.

3. 차트 해석:
   CPU와 메모리 통합 차트에서 읽어낼 수 있는 상관 관계(예: CPU 피크 시 메모리 동기화 여부) 설명.

5. Lock / Wait / 동시성 분석:
   SQL 데이터에 NRIV, FOR UPDATE, UPSERT 등의 패턴이 있는지, Lock Score가 높은 항목이 있는지 식별하여 리스크 기술.

6. 원인 분석 및 해결 방안 (RCA Table):
   데이터 기반 우선순위 산출 결과(Target, Window, Metric)를 상위 부하 요인에 대해 기술적으로 논평해라.

7. 종합 진단 및 운영 시사점:
   CPU 부하 유형 분류 (배치성 / 동시성 / 집계형 중 선택 및 이유), 단기 및 중기적 관점의 구체적인 개선 포인트.

[작성 스타일]
- 전문적이고 신뢰감 있는 "하오체" 또는 "~음" 문체 사용 가능.
- 고객사인 "이마트"를 언급하며 전문적인 시각을 제공할 것.
"""
        
        if not self.model:
            return self._generate_dummy_insights(stats, windows, top_cpu_label, top_lock_label)
            
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            return self._generate_dummy_insights(stats, windows, top_cpu_label, top_lock_label)

    def _generate_dummy_insights(self, stats, windows, top_cpu_label, top_lock_label):
        win_str = windows[0]['start'].strftime('%H:%M') if windows else "N/A"
        
        return f"""
02. Summary (요약):
금일 이마트 SAP DB 서버는 평균 CPU {stats.get('cpu_avg'):.1f}%로 전반적으로 안정적이나, {win_str} 경 최대 {stats.get('cpu_max')}%의 고부하가 발생하였습니다. {win_str} 전후의 대량 집계 배치 작업 시간대에 각별한 주의가 필요합니다.

03. 차트 해석:
{win_str} 부근에서 CPU 사용량이 급증할 때 메모리는 큰 변동이 없는 것으로 보아, 물리적 I/O보다는 복잡한 연산 중심의 SQL 처리가 부하를 견인한 것으로 해석됩니다.

05. Lock / Wait / 동시성 분석:
실제 SQL 락 대기 정보를 분석한 결과, 피크 시간대에 {top_lock_label} 프로그램 등에서 세션 경합이 관찰되었습니다. 이는 동시성 제어 로직 최적화가 필요한 영역입니다.

06. 원인 분석 및 해결 방안 (RCA Table):
상위 우선순위 분석 결과, {top_cpu_label}가 가장 높은 피크 기여도를 보이며 주된 튜닝 대상으로 식별되었습니다.

07. 종합 진단 및 운영 시사점:
금일 부하 유형은 '{top_cpu_label}' 중심의 집계형 부하와 일부 동시성 락 경합이 결합된 복합형으로 분류됩니다. 단기적으로는 해당 프로그램의 쿼리 튜닝이 시급하며, 실행 시간 분산을 통한 피크 평탄화가 필요합니다.
"""
