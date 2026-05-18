# scripts/test_db.py
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))
result = supabase.table("jobs").select("count").execute()
print("✅ DB connected:", result)