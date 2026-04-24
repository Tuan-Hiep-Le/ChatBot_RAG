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
    # 1. Định nghĩa mapping để nhận diện ngành và chuẩn hóa source
    branch_map = {
        "khoa học máy tính": "Khoa học máy tính và thông tin",
        "khoa học dữ liệu": "Khoa học dữ liệu",
        "toán học": "Toán học",
        "toán tin": "Toán tin",
        "khmt": "Khoa học máy tính và thông tin",
        "khdl": "Khoa học dữ liệu"
    }

    processed_query = user_query.lower()
    found_sources = []
    target_block = None
    # 1. TRÍCH XUẤT TẤT CẢ CÁC NGÀNH XUẤT HIỆN
    for key, full_name in branch_map.items():
        if key in processed_query:
            if full_name not in found_sources:
                found_sources.append(full_name)
   
    if "chung" in processed_query:
        target_block = "Khối kiến thức chung"
    elif "lĩnh vực" in processed_query:
        target_block = "Khối kiến thức theo lĩnh vực"
    elif "nhóm ngành" in processed_query:
        target_block = "Khối kiến thức theo nhóm ngành"
    elif "khối ngành" in processed_query:
        target_block = "Khối kiến thức theo khối ngành"
    elif "kiến thức ngành" in processed_query:
        target_block = "Khối kiến thức ngành"

    # 2. XÁC ĐỊNH CÓ PHẢI CÂU HỎI SO SÁNH KHÔNG
    is_comparison = any(word in processed_query for word in ["so sánh", "khác", "phân biệt", "đối chiếu"])
    
    # Nếu không phải so sánh thì mới xóa tên ngành để tránh nhiễu
    if not is_comparison and len(found_sources) == 1:
        search_query = f"Học phần {target_block if target_block else ''} ngành {found_sources[0]} {processed_query}"
    else:
        search_query = user_query 
    # 1. TRUY VẤN CHROMADB
    # Thay vì tính toán thủ công, ta dùng hàm query của collection
    config = get_retrieval_config_hybrid(processed_query, client)
     # Phải có dòng này trước khi gán where
    search_params = {
        "query_texts": [search_query],
        "n_results": config["n"],
        "include": ["documents", "metadatas", "distances"]
    }

    where_clauses = []
    if config["filter"]:
        where_clauses.append({"type": config["filter"]})
    
    if found_sources:
        if len(found_sources) > 1:
            where_clauses.append({"source": {"$in": found_sources}})
        else:
            where_clauses.append({"source": found_sources[0]})

    if target_block:
        # Lưu ý: ChromaDB dùng $contains để tìm chuỗi con trong metadata
        where_clauses.append({"khoi_kien_thuc": target_block})

    # Gộp filter
    if len(where_clauses) > 1:
        search_params["where"] = {"$and": where_clauses}
    elif len(where_clauses) == 1:
        search_params["where"] = where_clauses[0]
    else:
        # Trường hợp không có filter nào
        search_params.pop("where", None)

    print(f"🎯 Đang trỏ tìm kiếm với filter: {search_params.get('where')}")
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

    
    # --- ĐOẠN DEBUG CHUẨN (Dùng kết quả đã Filter) ---
    print("\n" + "="*30 + " [KIỂM TRA DỮ LIỆU THỰC TẾ GỬI CHO GEMINI] " + "="*30)
    # Sử dụng kết quả từ lần query đầu tiên (đã có search_params)
    if results['documents'] and len(results['documents'][0]) > 0:
        for i in range(len(results['documents'][0])):
            raw_content = results['documents'][0][i] 
            meta = results['metadatas'][0][i]
            dist = results['distances'][0][i] if 'distances' in results else "N/A"
        
            print(f"📍 Mảnh dữ liệu {i+1} [Khoảng cách: {dist:.4f}]")
            print(f"   📄 Nội dung thô: {raw_content[:200]}...") 
            print(f"   🏷️ Metadata: {meta}")
            print("-" * 40)
        else:
            print("⚠️ ChromaDB trống rỗng, không tìm thấy gì!")
        print("="*80 + "\n")

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