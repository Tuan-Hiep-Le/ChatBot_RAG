from dotenv import load_dotenv
from modules.router import get_retrieval_config_hybrid
import os

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME")

    

def rag_pipeline(user_query, collection, client):
    """
    Pipeline RAG tối ưu:
    1. Truy vấn trực tiếp từ ChromaDB (Vector Search).
    2. Lọc và ưu tiên các Chunk thuộc đúng ngành (target_branch).
    3. Trích xuất thông tin bằng Gemini với JSON Schema chuẩn.
    """
    synonyms = {"khoa học máy tính": "khmt", "khoa học dữ liệu": "khdl"}
    processed_query = user_query.lower()
    for long_form, short_form in synonyms.items():
        processed_query = processed_query.replace(long_form, short_form)
    # 1. TRUY VẤN CHROMADB
    # Thay vì tính toán thủ công, ta dùng hàm query của collection
    config = get_retrieval_config_hybrid(processed_query, client)

    search_params = {
        "query_texts": [processed_query],
        "n_results": config["n"],
        "include": ["documents", "metadatas"]
    }

    # 2. Thực hiện "Trỏ" dữ liệu (Filtering)
    if config["filter"]:
        # ChromaDB dùng tham số 'where' để lọc metadata
        search_params["where"] = {"type": config["filter"]}
        print(f"🎯 Đang trỏ tìm kiếm vào nhóm: {config['filter']}")

    results = collection.query(
        **search_params
    )

    retrieved_docs = results['documents'][0]
    retrieved_metas = results['metadatas'][0]

    # KIỂM TRA CẤP ĐỘ 1: Danh sách có rỗng không?
    if not retrieved_docs or len(retrieved_docs) == 0:
      print("⚠️ Warning: ChromaDB không tìm thấy đoạn văn bản nào!")
      return "Tôi không tìm thấy thông tin liên quan trong cơ sở dữ liệu."

    # 2. PHÂN LOẠI ƯU TIÊN (Priority Logic)
    # Tách riêng kiến thức đúng ngành và kiến thức liên quan

    all_context_parts = []

    for doc, meta in zip(retrieved_docs, retrieved_metas):
      source = meta.get('source', 'Không rõ ngành')
      all_context_parts.append(f"[Ngành: {source}]\n{doc}")

    # Gộp lại: Ưu tiên thông tin đúng ngành lên đầu để Prompt tập trung hơn
    context = "\n---\n".join(all_context_parts)
    if not context.strip():
      print("⚠️ Warning: Context sau khi gộp bị trống!")
      return "Dữ liệu tìm thấy không có nội dung hiển thị."

    # 3. XÂY DỰNG PROMPT (Sửa lỗi JSON Schema)
    prompt= f"""
Bạn là trợ lý tư vấn quy chế đào tạo của trường Đại học.
Nhiệm vụ: Dựa vào nội dung cung cấp để giải đáp thắc mắc của sinh viên.

Lưu ý:
1. Văn bản trích xuất từ OCR có thể dính chữ hoặc sai dấu, hãy dựa vào ngữ cảnh để suy luận.
2. Trình bày câu trả lời rõ ràng, dùng bullet points nếu có danh sách.
3. Nếu không có thông tin, hãy trả lời: "Tôi không tìm thấy thông tin cụ thể trong tài liệu quy chế.
4. Nếu câu hỏi mang tính khái quát, hãy tóm tắt ý chính.
5. Nếu sinh viên chào hỏi, hãy trả lời một cách thân thiện mà không cần trích ra quy chế.


Nội dung quy chế:
{context}

Câu hỏi của sinh viên: {user_query}
"""

    # 4. GỌI GEMINI VÀ XỬ LÝ KẾT QUẢ
    try:
        response = client.models.generate_content(
        model=MODEL_NAME, # Hoặc dùng biến MODEL_NAME
        contents=prompt
    )
        # Làm sạch chuỗi trả về để đảm bảo chỉ lấy phần JSON
        return response.text.strip()
    except Exception as e:
        return {"error": f"Lỗi xử lý dữ liệu: {str(e)}", "raw_response": response.text if 'response' in locals() else ""}

def rag_answer(user_query, collection, client):
    try:
        # Gọi pipeline mới (chỉ có 3 tham số)
        result = rag_pipeline(user_query, collection, client)

        print(f"\n🤖 Bot MIM trả lời:")
        print("-" * 50)
        print(result)
        print("-" * 50)
    except Exception as e:
        print(f"❌ Lỗi thực thi: {e}")