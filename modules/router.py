from dotenv import load_dotenv
import os

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME")

def get_hybrid_intent(user_query, client):
    query_lower = user_query.lower()

    # --- BƯỚC 1: THỦ THƯ MÁY MÓC (Dùng từ khóa để xử lý nhanh) ---
    if any(word in query_lower for word in ['tổng số', 'bao nhiêu tín chỉ', 'thời gian đào tạo']):
        return "MAJOR_TOTAL"
    
    # Nếu hỏi về ngoại ngữ/VSTEP
    if any(word in query_lower for word in ['vstep', 'ngoại ngữ', 'tiếng anh']):
        return "POLICY"

    # --- BƯỚC 2: THỦ THƯ THÔNG MINH (Chỉ dùng AI khi câu hỏi rối rắm hoặc không có từ khóa) ---
    # Trường hợp: Có cả 2 loại từ khóa (HYBRID) hoặc KHÔNG có từ khóa nào (Câu hỏi ẩn ý)
    print("🔍 Ý định chưa rõ ràng, đang nhờ Gemini phân loại...")

    prompt = f"""
    Phân loại câu hỏi sinh viên vào 1 nhóm:
    - MAJOR_TOTAL: Hỏi về tổng tín chỉ, thời gian học, tổng quan ngành.
    - MAJOR_SUBJECT: Hỏi về môn học cụ thể, mã môn, kiến thức môn học,liệt kê các môn học.
    - MAJOR_CAREER: Hỏi về cơ hội việc làm, đầu ra, mục tiêu ngành.
    - POLICY: Quy chế học vụ, điểm rèn luyện, chứng chỉ.
    - GENERAL: Chào hỏi, khen ngợi.
    Câu hỏi: "{user_query}"
    Trả về 1 từ duy nhất.
    """

    try:
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        intent = response.text.strip().upper()
        return intent if intent in ["MAJOR_TOTAL", "MAJOR_SUBJECT", "MAJOR_CAREER", "POLICY", "GENERAL"] else "POLICY"
    except:
        return "POLICY"

def get_retrieval_config_hybrid(user_query, client):
    intent = get_hybrid_intent(user_query, client)
    print("Intent: ",intent)
    mapping = {
        "MAJOR_TOTAL":   {"n": 5,  "filter": "tong_quan_tin_chi"},
        "MAJOR_SUBJECT": {"n": 20, "filter": "hoc_phan_dao_tao"},
        "MAJOR_CAREER":  {"n": 8,  "filter": "quy_che_van_ban"},
        "POLICY":        {"n": 15, "filter": "quy_che_chung"},
        "GENERAL":       {"n": 1,  "filter": None}
    }
    return mapping.get(intent, {"n": 5, "filter": None})
    
