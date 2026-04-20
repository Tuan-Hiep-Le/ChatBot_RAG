import uuid
from sentence_transformers import SentenceTransformer

model_sbert = SentenceTransformer(
    "keepitreal/vietnamese-sbert",
    device="cpu"
)
import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

# 1. Khai báo lớp Wrapper chuẩn hóa (Sửa lỗi Deprecation)
class VietnameseSBERTEmbedding(EmbeddingFunction):
    def __init__(self, model):
        self.model = model

    def __call__(self, input: Documents) -> Embeddings:
        # Trả về list các vector 768 chiều
        return self.model.encode(input, normalize_embeddings=True).tolist()

    def name(self) -> str:
        return "vietnamese-sbert-768"

# 2. Khởi tạo Client lưu trên ổ cứng
# Đảm bảo bạn dùng PersistentClient
client_chromadb = chromadb.PersistentClient(path="./vnu_vector_db")


# 3. Khởi tạo hàm embedding với model_sbert bạn đã có
viet_em_fn = VietnameseSBERTEmbedding(model=model_sbert)

# 4. Lấy hoặc tạo Collection (Sửa lỗi AttributeError)
collection = client_chromadb.get_or_create_collection(
    name="vnu_regulation_rag",
    embedding_function=viet_em_fn
)

def ingest_data_to_db(all_chunks, all_metadatas, collection, batch_size=50, clear_old_data=False):
    if not all_chunks:
        print("❌ Không có dữ liệu để nạp!")
        return

    # 1. Chỉ dọn dẹp nếu Hiệp yêu cầu
    if clear_old_data:
        try:
            current_count = collection.count()
            if current_count > 0:
                print(f"🧹 Đang dọn dẹp {current_count} dữ liệu cũ...")
                collection.delete(where={})
                print("✨ Đã xóa sạch dữ liệu cũ.")
        except Exception as e:
            print(f"❌ Lỗi khi xóa dữ liệu cũ: {e}")
    # 2. Chia nhỏ dữ liệu nạp (Giữ nguyên logic ID thông minh của Hiệp)
    total_chunks = len(all_chunks)
    for i in range(0, total_chunks, batch_size):
        batch_chunks = all_chunks[i : i + batch_size]
        batch_metas = all_metadatas[i : i + batch_size]

        # ID: Tên file + STT (Rất tốt để quản lý)
        batch_ids = [f"{m.get('source', 'chunk')}_{uuid.uuid4().hex[:8]}_{idx}"
                     for idx, m in enumerate(batch_metas, start=i)]
        try:
            collection.add(
            documents=batch_chunks,
            metadatas=batch_metas,
            ids=batch_ids
            )
            print(f"✅ Đã nạp xong {min(i + batch_size, total_chunks)}/{total_chunks}...")
        except Exception as e:
            print(f"❌ Lỗi khi nạp batch tại vị trí {i}: {e}")

    print(f"⭐ Hiện có tổng cộng {collection.count()} chunks trong DB.")

def setup_database():
    # Load model sbert một lần duy nhất
    from sentence_transformers import SentenceTransformer
    model_sbert = SentenceTransformer("keepitreal/vietnamese-sbert", device="cpu")
    viet_em_fn = VietnameseSBERTEmbedding(model=model_sbert)
    
    client_chromadb = chromadb.PersistentClient(path="./vnu_vector_db")
    collection = client_chromadb.get_or_create_collection(
        name="vnu_regulation_rag",
        embedding_function=viet_em_fn
    )
    return collection