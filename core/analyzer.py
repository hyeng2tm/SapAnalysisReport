import requests
import os
import logging
import json
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class SAPAIAnalyzer:
    def __init__(self):
        """Initialize Dev-X Agent connection"""
        self.keycloak_url = os.getenv("KEYCLOAK_URL")
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        self.agent_api_url = os.getenv("AGENT_API_URL")
        self.agent_id = os.getenv("AGENT_ID")
        self.agent_code = os.getenv("AGENT_CODE")
        self.agent_user = os.getenv("AGENT_USER", "sap-analyzer")
        self.s2s_token = None
        self._refresh_token()

    def _refresh_token(self):
        """Get S2S token from Keycloak"""
        if not all([self.keycloak_url, self.client_id, self.client_secret]):
            logger.warning("Dev-X credentials not found. AI insights will be generated in dummy mode.")
            return False
        
        try:
            token_payload = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }
            response = requests.post(self.keycloak_url, data=token_payload, timeout=10, verify=True)
            
            if response.status_code == 200:
                self.s2s_token = response.json().get("access_token")
                logger.info("Successfully obtained Dev-X S2S token")
                return True
            else:
                logger.warning(f"Failed to get token: {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Token refresh failed: {e}")
            return False

    def _is_unusable_ai_message(self, message):
        """Return True when agent body is a placeholder/error text, even if HTTP is 200."""
        if not message:
            return True
        msg = str(message).strip().lower()
        markers = [
            "ai 연결 안됨",
            "langgraph api error",
            "할당량 초과",
            "연결 실패",
        ]
        return any(m in msg for m in markers)

    def analyze_performance(self, stats, windows, peak_sql, top_locks, top_cpu_label="N/A", top_lock_label="N/A"):
        """Generates professional analysis report using Dev-X Agent"""
        windows_str = "\n".join([f"- {w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')} (Avg CPU: {w['avg_cpu']:.1f}%, Max CPU: {w['max_cpu']:.1f}%)" for w in windows])
        
        prompt = f"""
너는 시니어 SAP Basis / DB 성능 분석가로서, 고객사(이마트)의 SAP DB 서버 모니터링 리포트를 작성 중이다.
아래 제공되는 데이터들을 바탕으로 **전달된 7단계 구조**에 맞는 전문적인 한글 텍스트를 생성해라.
단, 리포트가 **세로(Portrait) 모드**이므로 텍스트가 너무 길어지지 않게 간결하고 명확하게 작성해라.

**[데이터 컨텍스트]**
- 날짜: {stats.get('date')}
- CPU: 평균 {stats.get('cpu_avg'):.1f}%, 최대 {stats.get('cpu_max')}%, 최소 {stats.get('cpu_min')}% (p95: {stats.get('cpu_p95'):.1f}%)
- 피크 샘플(80%↑): {stats.get('high_load_count')}회
- 메모리: 평균 {stats.get('mem_avg_pct'):.1f}% (안정적 관리 여부 판단 필요)
- 피크 구간: {windows_str}
- 부하 주범 프로그램: {top_cpu_label}
- 락 경합 프로그램: {top_lock_label}
- SQL 상세 지표: {peak_sql if peak_sql != 'None' else '정보 없음'}
- Lock Wait 상세 지표: {top_locks if top_locks != 'None' else '정보 없음'}

**[섹션별 작성 지침]**
아래 번호에 해당하는 내용을 상세히 작성해라.

1. Summary (요약): 
   전체 CPU 사용 수준 요약, 80% 초과 고부하 발생 여부, 메모리 안정성, 운영상 주의가 필요한 시간대 명시.

3. 차트 해석:
   CPU와 메모리 통합 차트에서 읽어낼 수 있는 상관 관계(예: CPU 피크 시 메모리 동기화 여부) 설명.

5. 부하 시간대 영향 SQL 및 프로그램 분석
   호출 횟수 (EXECUTION_COUNT),  총 실행 시간 (TOTAL_EXEC_TIME_SEC), 평균 / 최대 실행 시간, 사용 메모리 (MAX / TOTAL_EXECUTION_MEMORY_SIZE) 등을 기반으로 상위 SQL 및 프로그램의 영향도 분석.
    단, 표에 이미 있는 개별 SQL/프로그램 행을 다시 나열하지 말고, 반복 패턴, 공통 병목 특성, 운영 영향, 우선 조치 방향만 서술하라.
    데이터 컨텍스트의 [정량 SQL 지표] 표가 존재하면 해당 수치를 실제 값으로 인식하고, "실제 수치가 없어 정량 비교를 수행하지 않았다"와 같은 문구는 사용하지 말라.
   
6. 서비스 대기(Lock Wait) 분석:
   SQL 데이터에 NRIV, FOR UPDATE, UPSERT 등의 패턴이 있는지, Lock Score가 높은 항목이 있는지 식별하여 리스크 기술.
    데이터 컨텍스트의 [정량 Lock Wait 지표] 표가 존재하면 TOTAL_LOCK_WAIT_SEC, LOCK_WAIT_RATIO, SQL_TEXT를 실제 값으로 인식하고, "Lock Wait 상세 파일 확보 시 재분석 필요"와 같은 문구는 사용하지 말라.

7. 종합 진단 및 운영 시사점:
   CPU 부하 유형 분류 (배치성 / 동시성 / 집계형 중 선택 및 이유), 단기 및 중기적 관점의 구체적인 개선 포인트.

[작성 스타일]
- 전문적이고 신뢰감 있는 "하오체" 또는 "~음" 문체 사용 가능.
- 고객사인 "이마트"를 언급하며 전문적인 시각을 제공할 것.
"""
        
        logger.info(f"\n{'='*80}")
        logger.info("DEV-X AGENT ANALYSIS REQUEST")
        logger.info(f"{'='*80}")
        logger.info(f"[PROMPT (처음 5000자)]:\n{prompt[:5000]}...\n")
        
        if not self.s2s_token:
            logger.warning("No Dev-X token available. Using dummy insights.")
            return self._generate_dummy_insights(stats, windows, top_cpu_label, top_lock_label)
        
        result = self._call_dev_x_agent(
            prompt,
            stats=stats,
            windows=windows,
            top_cpu_label=top_cpu_label,
            top_lock_label=top_lock_label,
        )
        
        logger.info(f"\n{'='*80}")
        logger.info("DEV-X AGENT RESPONSE")
        logger.info(f"{'='*80}")
        logger.info(f"[RESULT (처음 1000자)]:\n{result[:1000] if result else 'No response'}...\n")
        
        return result

    def _call_dev_x_agent(self, prompt, stats=None, windows=None, top_cpu_label="N/A", top_lock_label="N/A"):
        """Call Dev-X Agent API with the analysis prompt"""
        try:
            headers = {
                "Authorization": f"Bearer {self.s2s_token}",
                "Content-Type": "application/json"
            }
            
            agent_request = {
                "query": prompt,
                "user": self.agent_user,
                "agent_id": self.agent_id,
                "agent_code": self.agent_code,
                "response_mode": "blocking",
                "conversation_id": None,
                "project_id": None,
                "inputs": {},
                "files": [],
                "materials": [],
                "templates": [],
                "references": [],
                "knowledge_ids": []
            }
            
            logger.info(f"\n[DEV-X REQUEST DETAILS]")
            logger.info(f"  URL: {self.agent_api_url}")
            logger.info(f"  Agent ID: {self.agent_id}")
            logger.info(f"  User: {self.agent_user}")
            logger.info(f"  Query Length: {len(prompt)} chars")
            
            response = requests.post(
                self.agent_api_url,
                headers=headers,
                json=agent_request,
                timeout=600,
                verify=True,
                stream=False,                
            )
            
            logger.info(f"Agent Response Status: {response.status_code}")
            logger.info(f"Agent Response Headers: {dict(response.headers)}")
            
            logger.info(f"\n[DEV-X RESPONSE STATUS]")
            logger.info(f"  Status Code: {response.status_code}")
            logger.info(f"  Content-Length: {len(response.text)} chars")
            
            if response.status_code == 200:
                
                result = response.json()
                
                logger.info(f"\n[DEV-X RESPONSE JSON STRUCTURE]")
                logger.info(f"  Keys: {list(result.keys())}")
                
                # Log the complete response for debugging
                logger.debug(f"  Full Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
                
                # Extract message from response (try answer first as it contains the actual analysis)
                final_message = None
                
                if "answer" in result:
                    final_message = result["answer"]
                    logger.info(f"  Extracted from 'answer' field")
                
                elif "message" in result and len(result["message"]) > 50:
                    # Only use 'message' if it's actually substantial (not just a status message like '처리 완료')
                    final_message = result["message"]
                    logger.info(f"  Extracted from 'message' field")
                
                elif "external_response" in result and isinstance(result["external_response"], dict):
                    ext_resp = result["external_response"]
                    if "answer" in ext_resp:
                        final_message = ext_resp["answer"]
                        logger.info(f"  Extracted from 'external_response.answer' field")
                    elif "message" in ext_resp:
                        final_message = ext_resp["message"]
                        logger.info(f"  Extracted from 'external_response.message' field")
                
                if final_message:
                    if self._is_unusable_ai_message(final_message):
                        logger.warning("Agent returned unusable placeholder text despite HTTP 200; using local fallback insights")
                        return self._generate_dummy_insights(
                            stats or {}, windows or [], top_cpu_label, top_lock_label
                        )
                    logger.info(f"\n[EXTRACTED MESSAGE]")
                    logger.info(f"  Length: {len(final_message)} chars")
                    logger.info(f"  Content (first 500 chars):\n{final_message[:500]}")
                    return final_message
                else:
                    logger.warning(f"No 'message' or 'answer' field found in response")
                    logger.warning(f"Available fields: {list(result.keys())}")
                    return self._generate_dummy_insights(
                        stats or {}, windows or [], top_cpu_label, top_lock_label
                    )
            else:
                logger.error(f"\n[DEV-X API ERROR]")
                logger.error(f"  Status: {response.status_code}")
                logger.error(f"  Response Text: {response.text[:500]}")
                return self._generate_dummy_insights(
                    stats or {}, windows or [], top_cpu_label, top_lock_label
                )
                
        except Exception as e:
            logger.error(f"\n[DEV-X API EXCEPTION]")
            logger.error(f"  Error Type: {type(e).__name__}")
            logger.error(f"  Error Message: {str(e)}")
            import traceback
            logger.error(f"  Traceback: {traceback.format_exc()}")
            return self._generate_dummy_insights(
                stats or {}, windows or [], top_cpu_label, top_lock_label
            )
    
    def _generate_dummy_insights_fallback(self):
        """Fallback insights when Dev-X API fails"""
        return """
1. Summary (요약):
외부 AI 응답 지연으로 자동 규칙 기반 임시 분석으로 대체됨.

3. 차트 출력 및 해석:
CPU/메모리 변동을 기준으로 핵심 구간을 우선 점검 필요.

5. Peak Load 구간별 SQL 영향도 분석:
호출량, 총 실행시간, 메모리 사용량이 큰 SQL을 우선 조치 대상으로 분류 권장.

6. 서비스 대기(Lock Wait) 분석:
Lock Wait 상위 SQL과 비율(LOCK_WAIT_RATIO)을 우선 확인 권장.

7. 종합 진단 및 기술적 제언:
[부하 원인 유형]:
배치성/동시성/집계형 중 데이터 특성 기준으로 분류 필요.

[개선 포인트]:
상위 SQL 인덱스 점검, 실행계획 재검토, 배치 시간대 분산 권장.

[최종 진단]:
외부 AI 응답 복구 전까지 자동 임시 분석 결과를 기준으로 운영 모니터링 수행.
"""

    def _generate_dummy_insights(self, stats, windows, top_cpu_label, top_lock_label):
        """Generate rule-based fallback insights when external AI is unavailable."""
        cpu_avg = float(stats.get('cpu_avg', 0) or 0)
        cpu_max = float(stats.get('cpu_max', 0) or 0)
        mem_avg = float(stats.get('mem_avg_pct', 0) or 0)
        high_load_count = int(stats.get('high_load_count', 0) or 0)
        window_text = ", ".join([
            f"{w['start'].strftime('%H:%M')}~{w['end'].strftime('%H:%M')}"
            for w in (windows or [])
        ]) or "주요 피크 구간 없음"

        return f"""
1. Summary (요약):
외부 AI 응답 지연으로 자동 규칙 기반 분석으로 대체함. CPU 평균 {cpu_avg:.1f}%, 최대 {cpu_max:.1f}%이며 80% 이상 고부하 샘플은 {high_load_count}회로 관측됨. 메모리 평균은 {mem_avg:.1f}% 수준이며 피크 구간은 {window_text}임.

3. 차트 출력 및 해석:
CPU 피크 구간과 메모리 추세의 동조 여부를 우선 점검 필요. CPU 급등 시 메모리도 동반 상승하면 집계성/대량 처리성 부하 가능성이 높고, CPU 단독 급등이면 동시성 처리나 짧은 배치성 처리 가능성이 큼.

5. Peak Load 구간별 SQL 영향도 분석:
부하 주범 프로그램 후보는 {top_cpu_label}로 식별됨. 총 실행시간과 총 메모리 사용량(TOTAL_EXECUTION_MEMORY_SIZE)을 기준으로 상위 SQL 우선순위를 적용해 튜닝 대상을 선정하는 것이 적절함.

6. 서비스 대기(Lock Wait) 분석:
락 경합 관점의 주요 프로그램 후보는 {top_lock_label}임. LOCK_WAIT_RATIO가 높은 구간의 SQL을 우선 확인하고, NRIV/FOR UPDATE/UPSERT 패턴이 반복되는지 점검 권장.

7. 종합 진단 및 운영 시사점:
[부하 원인 유형]:
피크 구간 분포와 SQL 집계 지표 기준으로 배치성 + 집계형 혼합 부하 가능성이 높음.

[개선 포인트]:
상위 SQL 실행계획 점검, 핵심 인덱스 보강, 배치 분산 실행, Lock 경합 구간의 트랜잭션 직렬화 정책 검토가 필요함.

[최종 진단]:
외부 AI 응답 복구 전에도 규칙 기반 분석 결과만으로 1차 운영 대응은 가능하며, 추후 AI 응답 복구 시 정밀 코멘트로 보강 권장.
"""
    
    def generate_specific_actions(self, global_top):
        """Generate specific remediation actions for top SQL/Lock items"""
        if global_top is None or global_top.empty:
            return None
        
        actions = []
        for _, row in global_top.iterrows():
            sql_label = str(row.get('SQL_LABEL', 'N/A'))[:60]
            exec_count = int(row.get('EXEC_COUNT_peak', 0))
            exec_time = float(row.get('AVG_EXEC_TIME_peak', 0))
            
            # Generate action based on execution characteristics
            if exec_count > 100 and exec_time < 0.1:
                action = "쿼리 배치 처리 및 인덱스 최적화"
            elif exec_time > 1.0:
                action = "Full Scan 제거 및 인덱스 생성"
            elif exec_count > 1000:
                action = "배치성 작업 시간 조정 및 병렬 처리"
            else:
                action = "SQL 튜닝 및 인덱스 최적화 권고"
            
            actions.append(action)
        
        return actions