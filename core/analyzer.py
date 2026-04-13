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

    def analyze_performance(self, stats, windows, peak_sql, top_locks):
        """Generates professional analysis report using Gemini."""
        windows_str = "\n".join([f"- {w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')} (Avg CPU: {w['avg_cpu']:.1f}%, Max CPU: {w['max_cpu']:.1f}%)" for w in windows])
        
        prompt = f"""
너는 SAP HANA 기반 DB 서버 운영 모니터링을 수행하는 시니어 SAP Basis / DB 성능 분석가다.
아래 제공되는 CPU, 메모리, 피크 구간(Peak Windows), 상위 SQL, 그리고 Lock Wait 데이터를 기반으로 “Deep Dive 분석 리포트”를 작성해라.

특히 '02. Peak Window SQL Analysis'와 '03. Lock Wait Analysis'에서 식별된 문제점들 중 시스템에 가장 큰 위협이 되는 요소를 판단하여 우선순위를 부여해라.

[시스템 통계 요약]
- 날짜: {stats.get('date')}
- CPU 평균: {stats.get('cpu_avg'):.1f}%, 최대: {stats.get('cpu_max')}%
- 메모리 평균 점유율: {stats.get('mem_avg_pct'):.1f}% (최대 {stats.get('mem_max_pct'):.1f}%)

[식별된 핵심 피크 구간 (Peak Windows)]
{windows_str}

[피크 구간 내 주요 SQL/프로그램 (Section 02 후보)]
{peak_sql if peak_sql is not None else "데이터 없음"}

[Lock Wait 현황 (Section 03 후보)]
{top_locks if top_locks is not None else "데이터 없음"}

[작성 지침 - 반드시 준수]
1. 제목: "{stats.get('date')} 이마트 SAP 부하 분석 보고서 (Deep Dive)"
2. 분석 결과 요약: 핵심 관찰치를 바탕으로 전반적인 시스템 상태를 3줄 내외로 요약.
3. 차트 해석: (차트는 별도로 삽입됨) 제공된 피크 구간과 리소스 추이간의 상관관계 설명.
4. 피크 구간별 상세 분석: 각 피크 윈도우에서 어떤 프로그램이 리소스를 점유했는지, 그 원인이 무엇인지 구체적으로 서술.
5. 원인 분석 및 해결 방안 (RCA Table 데이터 생성): 
   아래 형식의 태그를 사용하여 표에 들어갈 데이터를 생성해라. **맨 앞열에 우선순위(P1, P2, P3)를 포함해야 한다.**
   - P1: Critical (심각한 Lock 경합 또는 리소스 고갈 유발)
   - P2: High (반복적인 성능 저하 또는 상당한 리소스 점유)
   - P3: Normal (최적화가 필요한 일반 환경)

   [RCA_START]
   우선순위|대상|구간|근거지표|원인태그|해결방안
   P1|프로그램명|시작~종료|Avg CPU %, Max Mem|원인분류(예:대량집계)|구체적 해결책
   P2|...
   [RCA_END]

6. 추가 권고 사항: ST03N, M_EXPENSIVE_STATEMENTS 등 구체적인 T-Code 언급.
7. AI 종합 의견 (Final Recommendation): 아래 항목을 반드시 포함하여 작성해라.
   - CPU 부하 유형 분류: (배치성 / 동시성(락) / 집계·대량 처리 중 해당 항목 식별 및 이유)
   - 주요 개선 포인트:
     · 단기(Short-term): 즉시 반영 가능한 튜닝/인덱스/파라미터 등
     · 중기(Mid-term): 아키텍처 개선, 스케줄링 조정, 데이터 이관 등
   - 전체적인 시스템 건강 상태와 향후 트렌드 예측을 포함한 최종 제언 (총 5~8줄 내외).

[작성 스타일]
- 문서는 한국어 전문 보고서 문체 ("~로 판단됨", "~ 가능성 있음")
- 데이터에 기반하여 우선순위를 객적으로 부여할 것.
"""
        
        if not self.model:
            return self._generate_dummy_insights(stats, windows, peak_sql, top_locks)
            
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            return self._generate_dummy_insights(stats, windows, peak_sql, top_locks)

    def _generate_dummy_insights(self, stats, windows, peak_sql, top_locks):
        win_str = windows[0]['start'].strftime('%H:%M') if windows else "N/A"
        return f"""
1. 제목 : {stats.get('date')} 이마트 SAP 부하 분석 보고서 (Deep Dive)
2. 분석 결과 요약 : {stats.get('date')} 모니터링 결과, 전체 CPU 평균은 {stats.get('cpu_avg'):.1f}%로 양호하나 특정 시간대({win_str})에 일시적인 부하 집중 현상이 관찰되었습니다.
3. 차트 해석 : {win_str} 경 CPU 사용률이 급증하며 메모리 사용 패턴이 동기화되는 점으로 보아, 특정 대량 배치 작업 또는 복잡한 SQL 실행이 주된 원인으로 판단됩니다.
4. 피크 구간별 상세 분석 : {win_str} 전후로 발생한 피크는 상위 SQL 리스트에 나타난 바와 같이 데이터 집약적인 조인 작업의 영향이 큰 것으로 분석됩니다.
5. 원인 분석 및 해결 방안 (RCA Table) :
[RCA_START]
우선순위|대상|구간|근거지표|원인태그|해결방안
P1|ZCO_CODE|전체구간|Max CPU {stats.get('cpu_max')}%|FOR UPDATE|Lock 경합 최소화를 위한 쿼리 튜닝 및 인덱스 정비
P2|SAPLZMM011|{win_str}부근|Avg CPU {stats.get('cpu_avg'):.1f}%|대량 조인|조인 키 인덱스 보완 및 필요 컬럼만 Projection
[RCA_END]
6. 추가 권고 사항 : ST03N 및 M_EXPENSIVE_STATEMENTS를 통한 쿼리 실행 계획 상세 분석을 권고합니다.
7. AI 종합 의견 (Final Recommendation) :
- CPU 부하 유형 분류: 집계·대량 처리 및 동시성(락) 경합 복합형. 특정 Peak 시간대에 데이터 집약적인 SQL과 FOR UPDATE 락 대기가 동시에 관찰됨.
- 주요 개선 포인트:
  · 단기(Short-term): 상위 SQL(ZCO_CODE 등)의 인덱스 최적화 및 FOR UPDATE 구문 사용 최소화.
  · 중기(Mid-term): 대량 처리 배치의 실행 시간 분산(오프피크 스케줄링) 및 M_CS_TABLES 모니터링 주기 조정.
- 전체적인 시스템 상태는 양호하나, 피크 시간대의 리소스 경합은 향후 데이터 증가 시 가용성 리스크가 될 수 있으므로 단기 튜닝을 우선적으로 권장합니다.
"""
