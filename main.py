from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import datetime, timedelta
import httpx
import os
import json
import sqlite3
import threading
from typing import List, Dict, Optional
import uuid

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Environment variables loaded from .env file")
except ImportError:
    print("💡 python-dotenv not installed. Install with: pip install python-dotenv")
    print("   For now, make sure to set environment variables manually")

app = FastAPI(title="AI Calendar", description="A calendar with AI-powered daily summaries and cross-device sync")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DB_FILE = "calendar.db"
db_lock = threading.Lock()

def init_db():
    """Initialize the SQLite database"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                event_text TEXT NOT NULL,
                event_time TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_date ON events(date)
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id TEXT PRIMARY KEY,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_agent TEXT,
                ip_address TEXT
            )
        """)
        conn.commit()

def get_db():
    """Get database connection with proper locking"""
    return sqlite3.connect(DB_FILE, check_same_thread=False)

# Serve the frontend
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def read_root():
    return FileResponse("static/calendar.html")

# Serve manifest.json for PWA
@app.get("/manifest.json")
async def get_manifest():
    manifest = {
        "name": "AI Calendar",
        "short_name": "Calendar",
        "description": "Intelligent calendar with AI-powered summaries",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "theme_color": "#3b82f6",
        "background_color": "#ffffff",
        "scope": "/",
        "icons": [
            {
                "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'><rect width='192' height='192' fill='%233b82f6'/><text x='96' y='120' font-family='Arial' font-size='90' fill='white' text-anchor='middle'>📅</text></svg>",
                "sizes": "192x192",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            },
            {
                "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'><rect width='512' height='512' fill='%233b82f6'/><text x='256' y='320' font-family='Arial' font-size='240' fill='white' text-anchor='middle'>📅</text></svg>",
                "sizes": "512x512",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            }
        ],
        "categories": ["productivity", "utilities"],
        "lang": "en-US",
        "dir": "ltr"
    }
    return manifest

# Models
class Event(BaseModel):
    date: str  # "YYYY-MM-DD"
    event: str
    time: str = ""  # Optional time field "HH:MM"

class EventResponse(BaseModel):
    id: str
    date: str
    text: str
    time: Optional[str] = None
    created_at: str
    updated_at: str

# Get API key from environment
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    init_db()
    print("📊 Database initialized")
    print("🚀 AI Calendar server started")
    print("🔗 Cross-device sync enabled")
    if GOOGLE_API_KEY:
        print(f"🤖 Google AI configured (Key: {GOOGLE_API_KEY[:8]}...)")
    else:
        print("⚠️  Warning: GOOGLE_API_KEY not set - AI summaries will not work")
        print("   Set it with: export GOOGLE_API_KEY=your_key")
        print("   Or create a .env file with: GOOGLE_API_KEY=your_key")

@app.post("/events")
async def add_event(event: Event):
    """Add a new event to a specific date"""
    try:
        event_id = str(uuid.uuid4())
        
        with db_lock:
            with get_db() as conn:
                # Check for duplicates
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE date = ? AND event_text = ? AND event_time = ?",
                    (event.date, event.event, event.time or "")
                )
                if cursor.fetchone()[0] > 0:
                    # Return existing events if duplicate
                    cursor = conn.execute(
                        "SELECT id, date, event_text, event_time, created_at, updated_at FROM events WHERE date = ? ORDER BY event_time, created_at",
                        (event.date,)
                    )
                    events = []
                    for row in cursor.fetchall():
                        event_obj = {"id": row[0], "text": row[2]}
                        if row[3]:
                            event_obj["time"] = row[3]
                        events.append(event_obj)
                    return {"message": "Event already exists", "events": events}
                
                # Insert new event
                conn.execute(
                    "INSERT INTO events (id, date, event_text, event_time) VALUES (?, ?, ?, ?)",
                    (event_id, event.date, event.event, event.time or "")
                )
                conn.commit()
                
                # Return all events for the date
                cursor = conn.execute(
                    "SELECT id, date, event_text, event_time, created_at, updated_at FROM events WHERE date = ? ORDER BY event_time, created_at",
                    (event.date,)
                )
                events = []
                for row in cursor.fetchall():
                    event_obj = {"id": row[0], "text": row[2]}
                    if row[3]:
                        event_obj["time"] = row[3]
                    events.append(event_obj)
                
                return {"message": "Event added successfully", "events": events}
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding event: {str(e)}")

@app.get("/events")
async def get_events(date: str = Query(..., description="Date in YYYY-MM-DD format")):
    """Get all events for a specific date"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute(
                    "SELECT id, date, event_text, event_time, created_at, updated_at FROM events WHERE date = ? ORDER BY event_time, created_at",
                    (date,)
                )
                events = []
                for row in cursor.fetchall():
                    event_obj = {"id": row[0], "text": row[2]}
                    if row[3]:
                        event_obj["time"] = row[3]
                    events.append(event_obj)
                
                return {"date": date, "events": events}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching events: {str(e)}")

@app.get("/events/range")
async def get_events_range(
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format")
):
    """Get all events for a date range"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute(
                    "SELECT id, date, event_text, event_time, created_at, updated_at FROM events WHERE date BETWEEN ? AND ? ORDER BY date, event_time, created_at",
                    (start_date, end_date)
                )
                
                events_by_date = {}
                for row in cursor.fetchall():
                    date = row[1]
                    if date not in events_by_date:
                        events_by_date[date] = []
                    
                    event_obj = {"id": row[0], "text": row[2]}
                    if row[3]:
                        event_obj["time"] = row[3]
                    events_by_date[date].append(event_obj)
                
                # Fill in empty dates
                start = datetime.strptime(start_date, "%Y-%m-%d")
                end = datetime.strptime(end_date, "%Y-%m-%d")
                current = start
                while current <= end:
                    date_str = current.strftime("%Y-%m-%d")
                    if date_str not in events_by_date:
                        events_by_date[date_str] = []
                    current += timedelta(days=1)
                
                return {"start_date": start_date, "end_date": end_date, "events": events_by_date}
                
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching events range: {str(e)}")

@app.delete("/events")
async def delete_event(
    date: str = Query(...), 
    event: str = Query(...),
    time: str = Query(None, description="Optional time for the event"),
    event_id: str = Query(None, description="Optional event ID for precise deletion")
):
    """Delete a specific event from a date"""
    try:
        with db_lock:
            with get_db() as conn:
                if event_id:
                    # Delete by ID if provided (most precise)
                    cursor = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
                else:
                    # Delete by date, text, and optionally time
                    if time:
                        cursor = conn.execute(
                            "DELETE FROM events WHERE date = ? AND event_text = ? AND event_time = ?",
                            (date, event, time)
                        )
                    else:
                        cursor = conn.execute(
                            "DELETE FROM events WHERE date = ? AND event_text = ? AND (event_time = '' OR event_time IS NULL)",
                            (date, event)
                        )
                
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Event not found")
                
                conn.commit()
                
                # Return remaining events for the date
                cursor = conn.execute(
                    "SELECT id, date, event_text, event_time, created_at, updated_at FROM events WHERE date = ? ORDER BY event_time, created_at",
                    (date,)
                )
                events = []
                for row in cursor.fetchall():
                    event_obj = {"id": row[0], "text": row[2]}
                    if row[3]:
                        event_obj["time"] = row[3]
                    events.append(event_obj)
                
                return {"message": "Event deleted successfully", "events": events}
                
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting event: {str(e)}")

@app.get("/events/sync")
async def sync_events(last_sync: str = Query(None, description="Last sync timestamp")):
    """Get events modified since last sync for cross-device synchronization"""
    try:
        with db_lock:
            with get_db() as conn:
                if last_sync:
                    cursor = conn.execute(
                        "SELECT id, date, event_text, event_time, created_at, updated_at FROM events WHERE updated_at > ? ORDER BY updated_at",
                        (last_sync,)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT id, date, event_text, event_time, created_at, updated_at FROM events ORDER BY updated_at"
                    )
                
                events = []
                for row in cursor.fetchall():
                    event_obj = {
                        "id": row[0],
                        "date": row[1],
                        "text": row[2],
                        "time": row[3] if row[3] else None,
                        "created_at": row[4],
                        "updated_at": row[5]
                    }
                    events.append(event_obj)
                
                return {
                    "events": events,
                    "sync_timestamp": datetime.now().isoformat()
                }
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error syncing events: {str(e)}")

def build_prompt(date: str, events: list) -> str:
    """Build a prompt for the AI to generate a daily summary"""
    if not events:
        return (
            f"Today is {date} and I have no events scheduled. "
            "Please provide a brief, motivational message about making the most of a free day. "
            "Keep it concise and positive (2-3 sentences)."
        )
    
    # Handle both old string format and new dict format
    events_text = []
    for event in events:
        if isinstance(event, str):
            events_text.append(f"• {event}")
        elif isinstance(event, dict):
            time_str = f" at {event.get('time', '')}" if event.get('time') else ""
            events_text.append(f"• {event.get('text', '')}{time_str}")
    
    events_formatted = "\n".join(events_text)
    return (
        f"I'm planning my day for {date}. Here are my scheduled events:\n\n"
        f"{events_formatted}\n\n"
        "Please provide a concise, helpful daily overview that includes:\n"
        "1. A brief summary of my day\n"
        "2. Any suggestions for time management or preparation\n"
        "3. A motivational note\n\n"
        "Keep the response conversational and under 150 words."
    )

@app.get("/ai-summary")
async def ai_summary(date: str = Query(..., description="Date in YYYY-MM-DD format")):
    """Generate an AI-powered summary for the day's events"""
    
    if not GOOGLE_API_KEY:
        raise HTTPException(
            status_code=400, 
            detail="Google API key not configured. Please set the GOOGLE_API_KEY environment variable."
        )
    
    # Get events from database
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute(
                    "SELECT event_text, event_time FROM events WHERE date = ? ORDER BY event_time, created_at",
                    (date,)
                )
                events = []
                for row in cursor.fetchall():
                    event_obj = {"text": row[0]}
                    if row[1]:
                        event_obj["time"] = row[1]
                    events.append(event_obj)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching events for summary: {str(e)}")
    
    prompt = build_prompt(date, events)

    # Updated request format for Gemini API
    headers = {
        "Content-Type": "application/json",
    }

    body = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.7,
            "topK": 1,
            "topP": 1,
            "maxOutputTokens": 200,
        },
        "safetySettings": [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH", 
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}", 
                json=body, 
                headers=headers
            )
            
            if response.status_code == 401:
                raise HTTPException(
                    status_code=401, 
                    detail="Invalid API key. Please check your Google API key configuration."
                )
            elif response.status_code == 403:
                raise HTTPException(
                    status_code=403,
                    detail="API key doesn't have permission to access Gemini API. Please enable the Generative Language API in Google Cloud Console."
                )
            elif response.status_code != 200:
                error_detail = response.text
                raise HTTPException(
                    status_code=500, 
                    detail=f"Google API error ({response.status_code}): {error_detail}"
                )
            
            data = response.json()
            
            # Extract text from Gemini response format
            if "candidates" in data and len(data["candidates"]) > 0:
                candidate = data["candidates"][0]
                if "content" in candidate and "parts" in candidate["content"]:
                    text = candidate["content"]["parts"][0].get("text", "")
                    return {"date": date, "summary": text.strip()}
            
            return {"date": date, "summary": "Unable to generate summary at this time."}
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=408, detail="Request timed out. Please try again.")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        with get_db() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM events")
            event_count = cursor.fetchone()[0]
        
        return {
            "status": "healthy",
            "api_key_configured": bool(GOOGLE_API_KEY),
            "events_count": event_count,
            "database": "connected"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "database": "disconnected"
        }

@app.get("/stats")
async def get_stats():
    """Get calendar statistics"""
    try:
        with db_lock:
            with get_db() as conn:
                # Total events
                cursor = conn.execute("SELECT COUNT(*) FROM events")
                total_events = cursor.fetchone()[0]
                
                # Events this month
                current_month = datetime.now().strftime("%Y-%m")
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE date LIKE ?", 
                    (f"{current_month}%",)
                )
                month_events = cursor.fetchone()[0]
                
                # Busiest day
                cursor = conn.execute(
                    "SELECT date, COUNT(*) as count FROM events GROUP BY date ORDER BY count DESC LIMIT 1"
                )
                busiest_day = cursor.fetchone()
                
                return {
                    "total_events": total_events,
                    "events_this_month": month_events,
                    "busiest_day": {
                        "date": busiest_day[0] if busiest_day else None,
                        "event_count": busiest_day[1] if busiest_day else 0
                    }
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting stats: {str(e)}")

@app.post("/events/batch")
async def batch_add_events(events: List[Event]):
    """Add multiple events at once (useful for syncing)"""
    try:
        added_events = []
        with db_lock:
            with get_db() as conn:
                for event in events:
                    event_id = str(uuid.uuid4())
                    
                    # Check for duplicates
                    cursor = conn.execute(
                        "SELECT COUNT(*) FROM events WHERE date = ? AND event_text = ? AND event_time = ?",
                        (event.date, event.event, event.time or "")
                    )
                    if cursor.fetchone()[0] == 0:
                        # Insert new event
                        conn.execute(
                            "INSERT INTO events (id, date, event_text, event_time) VALUES (?, ?, ?, ?)",
                            (event_id, event.date, event.event, event.time or "")
                        )
                        added_events.append({
                            "id": event_id,
                            "date": event.date,
                            "text": event.event,
                            "time": event.time if event.time else None
                        })
                
                conn.commit()
                
        return {
            "message": f"Added {len(added_events)} events successfully",
            "added_events": added_events
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error batch adding events: {str(e)}")

@app.get("/export")
async def export_events(
    start_date: str = Query(None, description="Start date for export (YYYY-MM-DD)"),
    end_date: str = Query(None, description="End date for export (YYYY-MM-DD)")
):
    """Export events as JSON (useful for backup/migration)"""
    try:
        with db_lock:
            with get_db() as conn:
                if start_date and end_date:
                    cursor = conn.execute(
                        "SELECT id, date, event_text, event_time, created_at, updated_at FROM events WHERE date BETWEEN ? AND ? ORDER BY date, event_time, created_at",
                        (start_date, end_date)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT id, date, event_text, event_time, created_at, updated_at FROM events ORDER BY date, event_time, created_at"
                    )
                
                events = []
                for row in cursor.fetchall():
                    events.append({
                        "id": row[0],
                        "date": row[1],
                        "text": row[2],
                        "time": row[3] if row[3] else None,
                        "created_at": row[4],
                        "updated_at": row[5]
                    })
                
                return {
                    "events": events,
                    "export_date": datetime.now().isoformat(),
                    "total_count": len(events)
                }
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error exporting events: {str(e)}")

@app.delete("/events/all")
async def delete_all_events(confirm: str = Query(..., description="Type 'DELETE_ALL' to confirm")):
    """Delete all events (use with caution)"""
    if confirm != "DELETE_ALL":
        raise HTTPException(status_code=400, detail="Confirmation required")
    
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("DELETE FROM events")
                deleted_count = cursor.rowcount
                conn.commit()
                
        return {"message": f"Deleted {deleted_count} events successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting all events: {str(e)}")

# Get API key from environment (moved after dotenv loading)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Enhanced AI Calendar server...")
    print("📱 Mobile-optimized with offline support")
    print("🔄 Cross-device synchronization enabled")
    print("💾 SQLite database for data persistence")
    
    # Check API key status
    if GOOGLE_API_KEY:
        print(f"🤖 Google AI configured (Key: {GOOGLE_API_KEY[:8]}...)")
    else:
        print("⚠️  GOOGLE_API_KEY not found!")
        print("   Option 1: Set with command: GOOGLE_API_KEY=your_key python main.py")
        print("   Option 2: Create .env file with: GOOGLE_API_KEY=your_key")
        print("   Option 3: Export in terminal: export GOOGLE_API_KEY=your_key")
    
    print("🌐 Access the calendar at: http://localhost:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
