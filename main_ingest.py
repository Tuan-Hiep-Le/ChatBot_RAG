import os
from dotenv import load_dotenv
from google import genai
from sentence_transformers import SentenceTransformer
from ingest.processors import extract_folder_to_json_md
from ingest.chunking import process_general_documents, process_all_files
from ingest.database import VietnameseSBERTEmbedding, ingest_data_to_db,setup_database

load_dotenv()

# Cấu hình đường dẫn
PDF_CHUNG = "data_pdf/tai_lieu_chung"
PDF_NGANH = "data_pdf/tai_lieu_nganh"
MD_OUT = "data_pdf/data_markdown"
JSON_OUT = "data_pdf/data_json"
if __name__ == "__main__":
    print("🚀 Bắt đầu ingest dữ liệu...")

    # Để is_general=True, nó sẽ dùng PROMPT_QUY_CHE_CHUNG và lưu file .md
    extract_folder_to_json_md(PDF_CHUNG, MD_OUT, is_general=True)
    extract_folder_to_json_md(PDF_NGANH, JSON_OUT, is_general=False)

    chunks_tlc, metas_tlc = process_general_documents(MD_OUT)
    chunks_tln, metas_tln = process_all_files(JSON_OUT)

    collection = setup_database()
    ingest_data_to_db(chunks_tlc, metas_tlc, collection, clear_old_data=False)
    ingest_data_to_db(chunks_tln, metas_tln, collection, clear_old_data=False)

    print("✅ Hoàn tất ingest!")