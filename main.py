from fastapi import FastAPI, UploadFile, File, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
import os, json, re, io, requests
from dotenv import load_dotenv
from pypdf import PdfReader

# تحميل المتغيرات من ملف .env
load_dotenv()

CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")

if not CF_ACCOUNT_ID or not CF_API_TOKEN:
    raise RuntimeError("❌ مفيش مفاتيح كلاودفلير في ملف .env (CF_ACCOUNT_ID أو CF_API_TOKEN)")

app = FastAPI(title="More English Backend")

# السماح لمنصة More English بالتواصل مع السيرفر
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Keys المسموح بيها
VALID_API_KEYS = {"more-english-key-2024"}

def verify_key(api_key: str = Header(...)):
    if api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=401, detail="API Key غلط")
    return api_key

# دالة لاستخراج الـ JSON
def extract_json(text):
    if isinstance(text, (list, dict)):
        return text
        
    text = str(text).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise ValueError(f"مقدرناش نستخرج JSON من الرد: {text}")

# دالة التواصل مع كلاودفلير
def ask_cloudflare(prompt: str):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that only outputs valid JSON."},
            {"role": "user", "content": prompt}
        ]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"خطأ من كلاودفلير: {response.text}")
    
    result = response.json()
    return result["result"]["response"]

# دالة استخراج النص من الـ PDF
def extract_text_from_pdf(content: bytes):
    try:
        reader = PdfReader(io.BytesIO(content))
        pdf_text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"مقدرناش نقرا الـ PDF: {e}")

    if not pdf_text.strip():
        raise HTTPException(status_code=400, detail="ملف الـ PDF مفيهوش نص مقروء (ممكن يكون صور).")
    return pdf_text


# ============================================================
# 1. أداة الفلاش كاردز
# ============================================================
@app.post("/flashcards/generate")
async def generate_flashcards(
    file: UploadFile = File(...),
    api_key: str = Header(...)
):
    verify_key(api_key)
    content = await file.read()
    pdf_text = extract_text_from_pdf(content)

    prompt = f"""
    استخرج كل الكلمات الإنجليزية من النص ده مع معناها بالعربي.
    ارجع النتيجة بالشكل ده بس بدون أي كلام تاني (JSON Array فقط):
    [
        {{"english": "word", "arabic": "معنى"}},
        {{"english": "word", "arabic": "معنى"}}
    ]

    النص:
    {pdf_text[:3000]} 
    """

    try:
        ai_response = ask_cloudflare(prompt)
        cards = extract_json(ai_response)
        if isinstance(cards, dict) and "response" in cards:
             cards = extract_json(cards["response"])
        return {"flashcards": cards, "total": len(cards)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"رد غير صالح من كلاودفلير: {e}")


# ============================================================
# 2. أداة الكويز
# ============================================================
@app.post("/quiz/generate")
async def generate_quiz(
    file: UploadFile = File(...),
    api_key: str = Header(...)
):
    verify_key(api_key)
    content = await file.read()
    pdf_text = extract_text_from_pdf(content)

    prompt = f"""
    استخرج الأسئلة والإجابات من هذا الملف.
    ارجع النتيجة بالشكل ده بس بدون أي كلام تاني (JSON Array فقط):
    [
        {{
            "question": "السؤال هنا",
            "options": ["اختيار 1", "اختيار 2", "اختيار 3", "اختيار 4"],
            "correct_answer": "الإجابة الصح"
        }}
    ]

    النص:
    {pdf_text[:3000]} 
    """

    try:
        ai_response = ask_cloudflare(prompt)
        questions = extract_json(ai_response)
        if isinstance(questions, dict) and "response" in questions:
             questions = extract_json(questions["response"])
        return {"questions": questions, "total": len(questions)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"رد غير صالح من كلاودفلير: {e}")


# ============================================================
# 3. أداة خطة المذاكرة
# ============================================================
class Lecture(BaseModel):
    id: str
    title: str
    date: str
    completed: bool
    quiz_score: Optional[float] = None

class StudentData(BaseModel):
    student_name: str
    lectures: List[Lecture]
    available_hours_per_day: float
    exam_date: str

@app.post("/study-plan/generate")
async def generate_study_plan(
    data: StudentData,
    api_key: str = Header(...)
):
    verify_key(api_key)
    
    today = date.today().isoformat()
    
    lectures_info = "\n".join([
        f"- {l.title} | تاريخ: {l.date} | خلص: {'نعم' if l.completed else 'لا'} | درجة الكويز: {l.quiz_score or 'لم يحل'}"
        for l in data.lectures
    ])
    
    prompt = f"""
    أنت مساعد تعليمي. اعمل خطة مذاكرة للطالب {data.student_name}.
    
    المعلومات:
    - تاريخ اليوم: {today}
    - تاريخ الامتحان: {data.exam_date}
    - ساعات متاحة في اليوم: {data.available_hours_per_day}
    
    المحاضرات:
    {lectures_info}
    
    القاعدة:
    - الجديد = محاضرات الأسبوع الحالي = 70% من وقت المذاكرة
    - القديم = محاضرات قبل الأسبوع الحالي = 30% من وقت المذاكرة
    - لو درجة الكويز أقل من 60% زود وقت المراجعة على المحاضرة دي
    
    ارجع الخطة بالشكل ده بس بدون أي كلام تاني (JSON Object فقط):
    {{
        "daily_plan": [
            {{
                "date": "YYYY-MM-DD",
                "sessions": [
                    {{
                        "lecture": "اسم المحاضرة",
                        "type": "جديد أو مراجعة",
                        "duration_minutes": 45,
                        "pomodoros": 1
                    }}
                ]
            }}
        ],
        "summary": "ملخص الخطة"
    }}
    """
    
    try:
        ai_response = ask_cloudflare(prompt)
        plan = extract_json(ai_response)
        if isinstance(plan, dict) and "response" in plan:
             plan = extract_json(plan["response"])
        return plan
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"رد غير صالح من كلاودفلير: {e}")


# ============================================================
# 4. تجربة السيرفر
# ============================================================
@app.get("/")
def root():
    return {"message": "More English Backend شغال بكلاودفلير ✅ - كل الأدوات جاهزة"}