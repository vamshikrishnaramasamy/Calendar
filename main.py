from fastapi import FastAPI, HTTPException, Query, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import httpx
import os
import json
import sqlite3
import threading
from typing import List, Dict, Optional, Any, Union
import uuid
import hashlib
import mimetypes
from pathlib import Path
import shutil

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Environment variables loaded from .env file")
except ImportError:
    print("💡 python-dotenv not installed. Install with: pip install python-dotenv")
    print("   For now, make sure to set environment variables manually")

app = FastAPI(title="AI Workspace", description="A comprehensive Notion-like workspace with AI-powered features")

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
    """Initialize the SQLite database with all tables"""
    with sqlite3.connect(DB_FILE) as conn:
        # Workspaces table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                icon TEXT DEFAULT '🏠',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Pages table (like Notion pages)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                parent_id TEXT,
                title TEXT NOT NULL,
                icon TEXT DEFAULT '📄',
                cover_url TEXT,
                content TEXT DEFAULT '[]',
                page_type TEXT DEFAULT 'page',
                properties TEXT DEFAULT '{}',
                is_template BOOLEAN DEFAULT FALSE,
                is_archived BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
                FOREIGN KEY (parent_id) REFERENCES pages (id)
            )
        """)
        
        # Blocks table (content blocks within pages)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blocks (
                id TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                parent_id TEXT,
                type TEXT NOT NULL,
                content TEXT DEFAULT '{}',
                position INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (page_id) REFERENCES pages (id),
                FOREIGN KEY (parent_id) REFERENCES blocks (id)
            )
        """)
        
        # Databases table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS databases (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                icon TEXT DEFAULT '🗃️',
                schema TEXT DEFAULT '{}',
                view_config TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workspace_id) REFERENCES workspaces (id)
            )
        """)
        
        # Database records table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS database_records (
                id TEXT PRIMARY KEY,
                database_id TEXT NOT NULL,
                properties TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (database_id) REFERENCES databases (id)
            )
        """)
        
        # Files table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER,
                mime_type TEXT,
                file_hash TEXT,
                ai_analysis TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workspace_id) REFERENCES workspaces (id)
            )
        """)
        
        # Comments table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                page_id TEXT,
                block_id TEXT,
                content TEXT NOT NULL,
                author TEXT DEFAULT 'User',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (page_id) REFERENCES pages (id),
                FOREIGN KEY (block_id) REFERENCES blocks (id)
            )
        """)
        
        # Templates table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                category TEXT DEFAULT 'general',
                template_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Legacy events table (for calendar functionality)
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
        
        # Create indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_workspace ON pages(workspace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_parent ON pages(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_page ON blocks(page_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_parent ON blocks(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_database_records_db ON database_records(database_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_workspace ON files(workspace_id)")
        
        # Insert default workspace if none exists
        cursor = conn.execute("SELECT COUNT(*) FROM workspaces")
        if cursor.fetchone()[0] == 0:
            default_workspace_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO workspaces (id, name, description, icon) 
                VALUES (?, ?, ?, ?)
            """, (default_workspace_id, "My Workspace", "Default workspace", "🏠"))
            
            # Create default pages
            pages_to_create = [
                ("Getting Started", "📚", "page"),
                ("Calendar", "📅", "calendar"),
                ("Tasks", "✅", "database"),
                ("Notes", "📝", "page"),
                ("Projects", "🚀", "database")
            ]
            
            for title, icon, page_type in pages_to_create:
                page_id = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO pages (id, workspace_id, title, icon, page_type) 
                    VALUES (?, ?, ?, ?, ?)
                """, (page_id, default_workspace_id, title, icon, page_type))
            
            # Create default templates
            templates_to_create = [
                {
                    "name": "Meeting Notes",
                    "description": "Template for meeting notes with agenda, attendees, and action items",
                    "category": "meetings",
                    "template_data": {
                        "icon": "📝",
                        "page_type": "page",
                        "content": [
                            {"type": "heading", "content": {"text": "Meeting Notes", "level": 1}},
                            {"type": "paragraph", "content": {"text": "**Date:** " + datetime.now().strftime("%Y-%m-%d")}},
                            {"type": "paragraph", "content": {"text": "**Attendees:** "}},
                            {"type": "heading", "content": {"text": "Agenda", "level": 2}},
                            {"type": "bullet_list", "content": {"items": ["Topic 1", "Topic 2", "Topic 3"]}},
                            {"type": "heading", "content": {"text": "Discussion", "level": 2}},
                            {"type": "paragraph", "content": {"text": ""}},
                            {"type": "heading", "content": {"text": "Action Items", "level": 2}},
                            {"type": "todo_list", "content": {"items": [{"text": "Action item 1", "checked": False}]}}
                        ]
                    }
                },
                {
                    "name": "Project Plan",
                    "description": "Comprehensive project planning template",
                    "category": "projects",
                    "template_data": {
                        "icon": "🚀",
                        "page_type": "page",
                        "content": [
                            {"type": "heading", "content": {"text": "Project Plan", "level": 1}},
                            {"type": "heading", "content": {"text": "Overview", "level": 2}},
                            {"type": "paragraph", "content": {"text": "**Project Goal:** "}},
                            {"type": "paragraph", "content": {"text": "**Timeline:** "}},
                            {"type": "paragraph", "content": {"text": "**Team:** "}},
                            {"type": "heading", "content": {"text": "Phases", "level": 2}},
                            {"type": "heading", "content": {"text": "Phase 1: Planning", "level": 3}},
                            {"type": "bullet_list", "content": {"items": ["Define requirements", "Create timeline", "Assign roles"]}},
                            {"type": "heading", "content": {"text": "Phase 2: Development", "level": 3}},
                            {"type": "bullet_list", "content": {"items": ["Development task 1", "Development task 2"]}},
                            {"type": "heading", "content": {"text": "Phase 3: Testing & Launch", "level": 3}},
                            {"type": "bullet_list", "content": {"items": ["Testing", "Bug fixes", "Launch"]}}
                        ]
                    }
                },
                {
                    "name": "Daily Journal",
                    "description": "Daily reflection and planning template",
                    "category": "personal",
                    "template_data": {
                        "icon": "📔",
                        "page_type": "page",
                        "content": [
                            {"type": "heading", "content": {"text": "Daily Journal - " + datetime.now().strftime("%B %d, %Y"), "level": 1}},
                            {"type": "heading", "content": {"text": "Today's Goals", "level": 2}},
                            {"type": "todo_list", "content": {"items": [{"text": "Goal 1", "checked": False}, {"text": "Goal 2", "checked": False}]}},
                            {"type": "heading", "content": {"text": "Reflections", "level": 2}},
                            {"type": "paragraph", "content": {"text": "What went well today?"}},
                            {"type": "paragraph", "content": {"text": ""}},
                            {"type": "paragraph", "content": {"text": "What could be improved?"}},
                            {"type": "paragraph", "content": {"text": ""}},
                            {"type": "heading", "content": {"text": "Tomorrow's Priorities", "level": 2}},
                            {"type": "bullet_list", "content": {"items": ["Priority 1", "Priority 2", "Priority 3"]}}
                        ]
                    }
                },
                {
                    "name": "Task Database",
                    "description": "Comprehensive task management database",
                    "category": "productivity",
                    "template_data": {
                        "icon": "✅",
                        "page_type": "database",
                        "schema": {
                            "Task": {"type": "title"},
                            "Status": {"type": "select", "options": ["Not Started", "In Progress", "Completed", "Blocked"]},
                            "Priority": {"type": "select", "options": ["Low", "Medium", "High", "Urgent"]},
                            "Due Date": {"type": "date"},
                            "Assignee": {"type": "person"},
                            "Tags": {"type": "multi_select", "options": ["Work", "Personal", "Urgent", "Research"]}
                        }
                    }
                }
            ]
            
            for template_data in templates_to_create:
                template_id = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO templates (id, name, description, category, template_data) 
                    VALUES (?, ?, ?, ?, ?)
                """, (template_id, template_data["name"], template_data["description"], 
                      template_data["category"], json.dumps(template_data["template_data"])))
        
        conn.commit()

def get_db():
    """Get database connection with proper locking"""
    return sqlite3.connect(DB_FILE, check_same_thread=False)

# Serve the frontend
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def read_root():
    return FileResponse("static/workspace.html")

@app.get("/calendar")
async def read_calendar():
    return FileResponse("static/calendar.html")

# Serve manifest.json for PWA
@app.get("/manifest.json")
async def get_manifest():
    manifest = {
        "name": "AI Workspace",
        "short_name": "Workspace",
        "description": "Comprehensive Notion-like workspace with AI-powered features",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "theme_color": "#3b82f6",
        "background_color": "#ffffff",
        "scope": "/",
        "icons": [
            {
                "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'><rect width='192' height='192' fill='%233b82f6'/><text x='96' y='120' font-family='Arial' font-size='90' fill='white' text-anchor='middle'>🏠</text></svg>",
                "sizes": "192x192",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            },
            {
                "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'><rect width='512' height='512' fill='%233b82f6'/><text x='256' y='320' font-family='Arial' font-size='240' fill='white' text-anchor='middle'>🏠</text></svg>",
                "sizes": "512x512",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            }
        ],
        "categories": ["productivity", "utilities", "business"],
        "lang": "en-US",
        "dir": "ltr"
    }
    return manifest

# Models
class Workspace(BaseModel):
    name: str
    description: Optional[str] = None
    icon: str = "🏠"

class WorkspaceResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    icon: str
    created_at: str
    updated_at: str

class Page(BaseModel):
    workspace_id: str
    parent_id: Optional[str] = None
    title: str
    icon: str = "📄"
    cover_url: Optional[str] = None
    content: List[Dict[str, Any]] = Field(default_factory=list)
    page_type: str = "page"
    properties: Dict[str, Any] = Field(default_factory=dict)
    is_template: bool = False

class PageResponse(BaseModel):
    id: str
    workspace_id: str
    parent_id: Optional[str]
    title: str
    icon: str
    cover_url: Optional[str]
    content: List[Dict[str, Any]]
    page_type: str
    properties: Dict[str, Any]
    is_template: bool
    is_archived: bool
    created_at: str
    updated_at: str

class Block(BaseModel):
    page_id: str
    parent_id: Optional[str] = None
    type: str
    content: Dict[str, Any] = Field(default_factory=dict)
    position: int = 0

class BlockResponse(BaseModel):
    id: str
    page_id: str
    parent_id: Optional[str]
    type: str
    content: Dict[str, Any]
    position: int
    created_at: str
    updated_at: str

class Database(BaseModel):
    workspace_id: str
    name: str
    description: Optional[str] = None
    icon: str = "🗃️"
    schema: Dict[str, Any] = Field(default_factory=dict)
    view_config: Dict[str, Any] = Field(default_factory=dict)

class DatabaseResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: Optional[str]
    icon: str
    schema: Dict[str, Any]
    view_config: Dict[str, Any]
    created_at: str
    updated_at: str

class DatabaseRecord(BaseModel):
    database_id: str
    properties: Dict[str, Any] = Field(default_factory=dict)

class DatabaseRecordResponse(BaseModel):
    id: str
    database_id: str
    properties: Dict[str, Any]
    created_at: str
    updated_at: str

class Comment(BaseModel):
    page_id: Optional[str] = None
    block_id: Optional[str] = None
    content: str
    author: str = "User"

class CommentResponse(BaseModel):
    id: str
    page_id: Optional[str]
    block_id: Optional[str]
    content: str
    author: str
    created_at: str
    updated_at: str

class Template(BaseModel):
    name: str
    description: Optional[str] = None
    category: str = "general"
    template_data: Dict[str, Any]

class TemplateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    category: str
    template_data: Dict[str, Any]
    created_at: str

# Legacy calendar models
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
    
    # Create uploads directory
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)
    
    print("📊 Database initialized")
    print("🚀 AI Workspace server started")
    print("🔗 Cross-device sync enabled")
    print("📁 File uploads enabled")
    if GOOGLE_API_KEY:
        print(f"🤖 Google AI configured (Key: {GOOGLE_API_KEY[:8]}...)")
    else:
        print("⚠️  Warning: GOOGLE_API_KEY not set - AI features will not work")
        print("   Set it with: export GOOGLE_API_KEY=your_key")
        print("   Or create a .env file with: GOOGLE_API_KEY=your_key")

# ============================================================================
# WORKSPACE ENDPOINTS
# ============================================================================

@app.get("/workspaces", response_model=List[WorkspaceResponse])
async def get_workspaces():
    """Get all workspaces"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT id, name, description, icon, created_at, updated_at 
                    FROM workspaces ORDER BY created_at
                """)
                workspaces = []
                for row in cursor.fetchall():
                    workspaces.append({
                        "id": row[0],
                        "name": row[1],
                        "description": row[2],
                        "icon": row[3],
                        "created_at": row[4],
                        "updated_at": row[5]
                    })
                return workspaces
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching workspaces: {str(e)}")

@app.post("/workspaces", response_model=WorkspaceResponse)
async def create_workspace(workspace: Workspace):
    """Create a new workspace"""
    try:
        workspace_id = str(uuid.uuid4())
        with db_lock:
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO workspaces (id, name, description, icon) 
                    VALUES (?, ?, ?, ?)
                """, (workspace_id, workspace.name, workspace.description, workspace.icon))
                conn.commit()
                
                # Get the created workspace
                cursor = conn.execute("""
                    SELECT id, name, description, icon, created_at, updated_at 
                    FROM workspaces WHERE id = ?
                """, (workspace_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "icon": row[3],
                    "created_at": row[4],
                    "updated_at": row[5]
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating workspace: {str(e)}")

@app.put("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(workspace_id: str, workspace: Workspace):
    """Update a workspace"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    UPDATE workspaces 
                    SET name = ?, description = ?, icon = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (workspace.name, workspace.description, workspace.icon, workspace_id))
                
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Workspace not found")
                
                conn.commit()
                
                # Get the updated workspace
                cursor = conn.execute("""
                    SELECT id, name, description, icon, created_at, updated_at 
                    FROM workspaces WHERE id = ?
                """, (workspace_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "icon": row[3],
                    "created_at": row[4],
                    "updated_at": row[5]
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating workspace: {str(e)}")

@app.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str):
    """Delete a workspace and all its content"""
    try:
        with db_lock:
            with get_db() as conn:
                # Check if workspace exists
                cursor = conn.execute("SELECT COUNT(*) FROM workspaces WHERE id = ?", (workspace_id,))
                if cursor.fetchone()[0] == 0:
                    raise HTTPException(status_code=404, detail="Workspace not found")
                
                # Delete all related data (cascade delete)
                conn.execute("DELETE FROM database_records WHERE database_id IN (SELECT id FROM databases WHERE workspace_id = ?)", (workspace_id,))
                conn.execute("DELETE FROM databases WHERE workspace_id = ?", (workspace_id,))
                conn.execute("DELETE FROM blocks WHERE page_id IN (SELECT id FROM pages WHERE workspace_id = ?)", (workspace_id,))
                conn.execute("DELETE FROM comments WHERE page_id IN (SELECT id FROM pages WHERE workspace_id = ?)", (workspace_id,))
                conn.execute("DELETE FROM pages WHERE workspace_id = ?", (workspace_id,))
                conn.execute("DELETE FROM files WHERE workspace_id = ?", (workspace_id,))
                conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
                
                conn.commit()
                
                return {"message": "Workspace deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting workspace: {str(e)}")

# ============================================================================
# PAGE ENDPOINTS
# ============================================================================

@app.get("/workspaces/{workspace_id}/pages", response_model=List[PageResponse])
async def get_pages(workspace_id: str, parent_id: Optional[str] = Query(None)):
    """Get pages in a workspace, optionally filtered by parent"""
    try:
        with db_lock:
            with get_db() as conn:
                if parent_id:
                    cursor = conn.execute("""
                        SELECT id, workspace_id, parent_id, title, icon, cover_url, content, 
                               page_type, properties, is_template, is_archived, created_at, updated_at
                        FROM pages WHERE workspace_id = ? AND parent_id = ? AND is_archived = FALSE
                        ORDER BY created_at
                    """, (workspace_id, parent_id))
                else:
                    cursor = conn.execute("""
                        SELECT id, workspace_id, parent_id, title, icon, cover_url, content, 
                               page_type, properties, is_template, is_archived, created_at, updated_at
                        FROM pages WHERE workspace_id = ? AND parent_id IS NULL AND is_archived = FALSE
                        ORDER BY created_at
                    """, (workspace_id,))
                
                pages = []
                for row in cursor.fetchall():
                    pages.append({
                        "id": row[0],
                        "workspace_id": row[1],
                        "parent_id": row[2],
                        "title": row[3],
                        "icon": row[4],
                        "cover_url": row[5],
                        "content": json.loads(row[6]) if row[6] else [],
                        "page_type": row[7],
                        "properties": json.loads(row[8]) if row[8] else {},
                        "is_template": bool(row[9]),
                        "is_archived": bool(row[10]),
                        "created_at": row[11],
                        "updated_at": row[12]
                    })
                return pages
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching pages: {str(e)}")

@app.get("/pages/{page_id}", response_model=PageResponse)
async def get_page(page_id: str):
    """Get a specific page"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT id, workspace_id, parent_id, title, icon, cover_url, content, 
                           page_type, properties, is_template, is_archived, created_at, updated_at
                    FROM pages WHERE id = ?
                """, (page_id,))
                row = cursor.fetchone()
                
                if not row:
                    raise HTTPException(status_code=404, detail="Page not found")
                
                return {
                    "id": row[0],
                    "workspace_id": row[1],
                    "parent_id": row[2],
                    "title": row[3],
                    "icon": row[4],
                    "cover_url": row[5],
                    "content": json.loads(row[6]) if row[6] else [],
                    "page_type": row[7],
                    "properties": json.loads(row[8]) if row[8] else {},
                    "is_template": bool(row[9]),
                    "is_archived": bool(row[10]),
                    "created_at": row[11],
                    "updated_at": row[12]
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching page: {str(e)}")

@app.post("/pages", response_model=PageResponse)
async def create_page(page: Page):
    """Create a new page"""
    try:
        page_id = str(uuid.uuid4())
        with db_lock:
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO pages (id, workspace_id, parent_id, title, icon, cover_url, 
                                     content, page_type, properties, is_template) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (page_id, page.workspace_id, page.parent_id, page.title, page.icon, 
                      page.cover_url, json.dumps(page.content), page.page_type, 
                      json.dumps(page.properties), page.is_template))
                conn.commit()
                
                # Get the created page
                cursor = conn.execute("""
                    SELECT id, workspace_id, parent_id, title, icon, cover_url, content, 
                           page_type, properties, is_template, is_archived, created_at, updated_at
                    FROM pages WHERE id = ?
                """, (page_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "workspace_id": row[1],
                    "parent_id": row[2],
                    "title": row[3],
                    "icon": row[4],
                    "cover_url": row[5],
                    "content": json.loads(row[6]) if row[6] else [],
                    "page_type": row[7],
                    "properties": json.loads(row[8]) if row[8] else {},
                    "is_template": bool(row[9]),
                    "is_archived": bool(row[10]),
                    "created_at": row[11],
                    "updated_at": row[12]
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating page: {str(e)}")

@app.put("/pages/{page_id}", response_model=PageResponse)
async def update_page(page_id: str, page: Page):
    """Update a page"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    UPDATE pages 
                    SET title = ?, icon = ?, cover_url = ?, content = ?, 
                        page_type = ?, properties = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (page.title, page.icon, page.cover_url, json.dumps(page.content),
                      page.page_type, json.dumps(page.properties), page_id))
                
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Page not found")
                
                conn.commit()
                
                # Get the updated page
                cursor = conn.execute("""
                    SELECT id, workspace_id, parent_id, title, icon, cover_url, content, 
                           page_type, properties, is_template, is_archived, created_at, updated_at
                    FROM pages WHERE id = ?
                """, (page_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "workspace_id": row[1],
                    "parent_id": row[2],
                    "title": row[3],
                    "icon": row[4],
                    "cover_url": row[5],
                    "content": json.loads(row[6]) if row[6] else [],
                    "page_type": row[7],
                    "properties": json.loads(row[8]) if row[8] else {},
                    "is_template": bool(row[9]),
                    "is_archived": bool(row[10]),
                    "created_at": row[11],
                    "updated_at": row[12]
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating page: {str(e)}")

@app.delete("/pages/{page_id}")
async def delete_page(page_id: str, permanent: bool = Query(False)):
    """Delete or archive a page"""
    try:
        with db_lock:
            with get_db() as conn:
                if permanent:
                    # Permanent delete
                    conn.execute("DELETE FROM blocks WHERE page_id = ?", (page_id,))
                    conn.execute("DELETE FROM comments WHERE page_id = ?", (page_id,))
                    cursor = conn.execute("DELETE FROM pages WHERE id = ?", (page_id,))
                else:
                    # Archive
                    cursor = conn.execute("""
                        UPDATE pages SET is_archived = TRUE, updated_at = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    """, (page_id,))
                
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Page not found")
                
                conn.commit()
                
                return {"message": "Page deleted successfully" if permanent else "Page archived successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting page: {str(e)}")

# ============================================================================
# DATABASE ENDPOINTS
# ============================================================================

@app.get("/workspaces/{workspace_id}/databases", response_model=List[DatabaseResponse])
async def get_databases(workspace_id: str):
    """Get all databases in a workspace"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT id, workspace_id, name, description, icon, schema, view_config, created_at, updated_at
                    FROM databases WHERE workspace_id = ? ORDER BY created_at
                """, (workspace_id,))
                
                databases = []
                for row in cursor.fetchall():
                    databases.append({
                        "id": row[0],
                        "workspace_id": row[1],
                        "name": row[2],
                        "description": row[3],
                        "icon": row[4],
                        "schema": json.loads(row[5]) if row[5] else {},
                        "view_config": json.loads(row[6]) if row[6] else {},
                        "created_at": row[7],
                        "updated_at": row[8]
                    })
                return databases
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching databases: {str(e)}")

@app.get("/databases/{database_id}", response_model=DatabaseResponse)
async def get_database(database_id: str):
    """Get a specific database"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT id, workspace_id, name, description, icon, schema, view_config, created_at, updated_at
                    FROM databases WHERE id = ?
                """, (database_id,))
                row = cursor.fetchone()
                
                if not row:
                    raise HTTPException(status_code=404, detail="Database not found")
                
                return {
                    "id": row[0],
                    "workspace_id": row[1],
                    "name": row[2],
                    "description": row[3],
                    "icon": row[4],
                    "schema": json.loads(row[5]) if row[5] else {},
                    "view_config": json.loads(row[6]) if row[6] else {},
                    "created_at": row[7],
                    "updated_at": row[8]
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting database: {str(e)}")

@app.post("/databases", response_model=DatabaseResponse)
async def create_database(database: Database):
    """Create a new database"""
    try:
        database_id = str(uuid.uuid4())
        with db_lock:
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO databases (id, workspace_id, name, description, icon, schema, view_config) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (database_id, database.workspace_id, database.name, database.description, 
                      database.icon, json.dumps(database.schema), json.dumps(database.view_config)))
                conn.commit()
                
                # Get the created database
                cursor = conn.execute("""
                    SELECT id, workspace_id, name, description, icon, schema, view_config, created_at, updated_at
                    FROM databases WHERE id = ?
                """, (database_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "workspace_id": row[1],
                    "name": row[2],
                    "description": row[3],
                    "icon": row[4],
                    "schema": json.loads(row[5]) if row[5] else {},
                    "view_config": json.loads(row[6]) if row[6] else {},
                    "created_at": row[7],
                    "updated_at": row[8]
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating database: {str(e)}")

@app.post("/ai/analyze-database/{database_id}")
async def analyze_database_with_ai(database_id: str):
    """Analyze a database using AI"""
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=400, detail="Google API key not configured")
    
    try:
        with db_lock:
            with get_db() as conn:
                # Get database info
                cursor = conn.execute("""
                    SELECT name, description, schema FROM databases WHERE id = ?
                """, (database_id,))
                db_row = cursor.fetchone()
                
                if not db_row:
                    raise HTTPException(status_code=404, detail="Database not found")
                
                db_name, db_description, schema_json = db_row
                schema = json.loads(schema_json) if schema_json else {}
                
                # Get records
                cursor = conn.execute("""
                    SELECT properties FROM database_records WHERE database_id = ? LIMIT 10
                """, (database_id,))
                
                sample_records = []
                for row in cursor.fetchall():
                    properties = json.loads(row[0]) if row[0] else {}
                    sample_records.append(properties)
                
                # Get total count
                cursor = conn.execute("SELECT COUNT(*) FROM database_records WHERE database_id = ?", (database_id,))
                total_records = cursor.fetchone()[0]
                
                analysis_prompt = f"""
                Look at this database and give me a brief, simple description of what it contains:
                
                Database: {db_name}
                Total Records: {total_records}
                Fields: {', '.join(schema.keys()) if schema else 'No fields defined'}
                
                Sample data:
                {json.dumps(sample_records[:3], indent=2) if sample_records else 'No data'}
                
                Just tell me in 1-2 sentences what this database is about and what kind of information it stores. Keep it simple and brief.
                """
                
                headers = {"Content-Type": "application/json"}
                body = {
                    "contents": [{"parts": [{"text": analysis_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 600,
                    }
                }
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                        json=body,
                        headers=headers
                    )
                    
                    if response.status_code != 200:
                        raise HTTPException(status_code=500, detail=f"AI API error: {response.text}")
                    
                    data = response.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            analysis = candidate["content"]["parts"][0].get("text", "")
                            return {"analysis": analysis.strip()}
                    
                    return {"analysis": "Unable to analyze database at this time."}
                    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing database: {str(e)}")

@app.get("/databases/{database_id}/records", response_model=List[DatabaseRecordResponse])
async def get_database_records(database_id: str):
    """Get all records in a database"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT id, database_id, properties, created_at, updated_at
                    FROM database_records WHERE database_id = ? ORDER BY created_at
                """, (database_id,))
                
                records = []
                for row in cursor.fetchall():
                    records.append({
                        "id": row[0],
                        "database_id": row[1],
                        "properties": json.loads(row[2]) if row[2] else {},
                        "created_at": row[3],
                        "updated_at": row[4]
                    })
                return records
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching database records: {str(e)}")

@app.post("/database-records", response_model=DatabaseRecordResponse)
async def create_database_record(record: DatabaseRecord):
    """Create a new database record"""
    try:
        record_id = str(uuid.uuid4())
        with db_lock:
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO database_records (id, database_id, properties) 
                    VALUES (?, ?, ?)
                """, (record_id, record.database_id, json.dumps(record.properties)))
                conn.commit()
                
                # Get the created record
                cursor = conn.execute("""
                    SELECT id, database_id, properties, created_at, updated_at
                    FROM database_records WHERE id = ?
                """, (record_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "database_id": row[1],
                    "properties": json.loads(row[2]) if row[2] else {},
                    "created_at": row[3],
                    "updated_at": row[4]
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating database record: {str(e)}")

@app.put("/database-records/{record_id}", response_model=DatabaseRecordResponse)
async def update_database_record(record_id: str, record: DatabaseRecord):
    """Update a database record"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    UPDATE database_records 
                    SET properties = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (json.dumps(record.properties), record_id))
                
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Record not found")
                
                conn.commit()
                
                # Get the updated record
                cursor = conn.execute("""
                    SELECT id, database_id, properties, created_at, updated_at
                    FROM database_records WHERE id = ?
                """, (record_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "database_id": row[1],
                    "properties": json.loads(row[2]) if row[2] else {},
                    "created_at": row[3],
                    "updated_at": row[4]
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating database record: {str(e)}")

@app.delete("/database-records/{record_id}")
async def delete_database_record(record_id: str):
    """Delete a database record"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("DELETE FROM database_records WHERE id = ?", (record_id,))
                
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Record not found")
                
                conn.commit()
                
                return {"message": "Record deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting database record: {str(e)}")

@app.post("/import-database")
async def import_database(workspace_id: str, file: UploadFile = File(...)):
    """Import data from CSV/JSON file to create a database"""
    import csv
    import io
    import json
    
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")
        
        # Read file content
        content = await file.read()
        
        # Parse based on file type
        data = []
        schema = {}
        
        if file.filename.endswith('.csv'):
            # Parse CSV
            csv_content = content.decode('utf-8')
            csv_reader = csv.DictReader(io.StringIO(csv_content))
            
            for row in csv_reader:
                data.append(row)
            
            # Generate schema from first few rows
            if data:
                for key, value in data[0].items():
                    # Try to determine data type
                    if value and value.isdigit():
                        schema[key] = {"type": "number"}
                    elif value and value.lower() in ['true', 'false']:
                        schema[key] = {"type": "checkbox"}
                    elif value and '@' in value and '.' in value:
                        schema[key] = {"type": "email"}
                    else:
                        schema[key] = {"type": "text"}
        
        elif file.filename.endswith('.json'):
            # Parse JSON
            json_data = json.loads(content.decode('utf-8'))
            
            if isinstance(json_data, list):
                data = json_data
            elif isinstance(json_data, dict):
                data = [json_data]
            else:
                raise HTTPException(status_code=400, detail="Invalid JSON format")
            
            # Generate schema
            if data:
                for key, value in data[0].items():
                    if isinstance(value, bool):
                        schema[key] = {"type": "checkbox"}
                    elif isinstance(value, (int, float)):
                        schema[key] = {"type": "number"}
                    elif isinstance(value, str):
                        if '@' in value and '.' in value:
                            schema[key] = {"type": "email"}
                        else:
                            schema[key] = {"type": "text"}
                    else:
                        schema[key] = {"type": "text"}
        
        else:
            raise HTTPException(status_code=400, detail="Unsupported file format. Please use CSV or JSON.")
        
        if not data:
            raise HTTPException(status_code=400, detail="No data found in file")
        
        # Create database
        database_name = file.filename.rsplit('.', 1)[0]  # Remove extension
        database_id = str(uuid.uuid4())
        
        with db_lock:
            with get_db() as conn:
                # Create database
                conn.execute("""
                    INSERT INTO databases (id, workspace_id, name, description, icon, schema, view_config) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (database_id, workspace_id, database_name, 
                      f"Imported from {file.filename}", "📊", 
                      json.dumps(schema), json.dumps({})))
                
                # Insert records
                records_added = 0
                for row_data in data:
                    record_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO database_records (id, database_id, properties) 
                        VALUES (?, ?, ?)
                    """, (record_id, database_id, json.dumps(row_data)))
                    records_added += 1
                
                conn.commit()
        
        return {
            "message": "Database imported successfully",
            "database_id": database_id,
            "database_name": database_name,
            "records_count": records_added,
            "schema": schema
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error importing database: {str(e)}")

# ============================================================================
# BLOCK ENDPOINTS
# ============================================================================

@app.get("/pages/{page_id}/blocks", response_model=List[BlockResponse])
async def get_blocks(page_id: str):
    """Get all blocks in a page"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT id, page_id, parent_id, type, content, position, created_at, updated_at
                    FROM blocks WHERE page_id = ? ORDER BY position, created_at
                """, (page_id,))
                
                blocks = []
                for row in cursor.fetchall():
                    blocks.append({
                        "id": row[0],
                        "page_id": row[1],
                        "parent_id": row[2],
                        "type": row[3],
                        "content": json.loads(row[4]) if row[4] else {},
                        "position": row[5],
                        "created_at": row[6],
                        "updated_at": row[7]
                    })
                return blocks
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching blocks: {str(e)}")

@app.post("/blocks", response_model=BlockResponse)
async def create_block(block: Block):
    """Create a new block"""
    try:
        block_id = str(uuid.uuid4())
        with db_lock:
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO blocks (id, page_id, parent_id, type, content, position) 
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (block_id, block.page_id, block.parent_id, block.type, 
                      json.dumps(block.content), block.position))
                conn.commit()
                
                # Get the created block
                cursor = conn.execute("""
                    SELECT id, page_id, parent_id, type, content, position, created_at, updated_at
                    FROM blocks WHERE id = ?
                """, (block_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "page_id": row[1],
                    "parent_id": row[2],
                    "type": row[3],
                    "content": json.loads(row[4]) if row[4] else {},
                    "position": row[5],
                    "created_at": row[6],
                    "updated_at": row[7]
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating block: {str(e)}")

@app.put("/blocks/{block_id}", response_model=BlockResponse)
async def update_block(block_id: str, block: Block):
    """Update a block"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    UPDATE blocks 
                    SET type = ?, content = ?, position = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (block.type, json.dumps(block.content), block.position, block_id))
                
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Block not found")
                
                conn.commit()
                
                # Get the updated block
                cursor = conn.execute("""
                    SELECT id, page_id, parent_id, type, content, position, created_at, updated_at
                    FROM blocks WHERE id = ?
                """, (block_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "page_id": row[1],
                    "parent_id": row[2],
                    "type": row[3],
                    "content": json.loads(row[4]) if row[4] else {},
                    "position": row[5],
                    "created_at": row[6],
                    "updated_at": row[7]
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating block: {str(e)}")

@app.delete("/blocks/{block_id}")
async def delete_block(block_id: str):
    """Delete a block"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("DELETE FROM blocks WHERE id = ?", (block_id,))
                
                if cursor.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Block not found")
                
                conn.commit()
                
                return {"message": "Block deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting block: {str(e)}")

# ============================================================================
# FILE UPLOAD ENDPOINTS
# ============================================================================

@app.post("/upload")
async def upload_file(workspace_id: str, file: UploadFile = File(...)):
    """Upload a file to a workspace"""
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")
        
        # Generate unique filename
        file_id = str(uuid.uuid4())
        file_extension = Path(file.filename).suffix
        filename = f"{file_id}{file_extension}"
        file_path = Path("uploads") / filename
        
        # Calculate file hash
        content = await file.read()
        file_hash = hashlib.sha256(content).hexdigest()
        
        # Save file
        with open(file_path, "wb") as f:
            f.write(content)
        
        # Save to database
        with db_lock:
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO files (id, workspace_id, filename, original_name, file_path, 
                                     file_size, mime_type, file_hash) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (file_id, workspace_id, filename, file.filename, str(file_path),
                      len(content), file.content_type, file_hash))
                conn.commit()
        
        return {
            "id": file_id,
            "filename": filename,
            "original_name": file.filename,
            "file_size": len(content),
            "mime_type": file.content_type,
            "url": f"/files/{file_id}"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error uploading file: {str(e)}")

@app.get("/files/{file_id}")
async def get_file(file_id: str):
    """Get a file by ID"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT filename, original_name, file_path, mime_type
                    FROM files WHERE id = ?
                """, (file_id,))
                row = cursor.fetchone()
                
                if not row:
                    raise HTTPException(status_code=404, detail="File not found")
                
                file_path = Path(row[2])
                if not file_path.exists():
                    raise HTTPException(status_code=404, detail="File not found on disk")
                
                return FileResponse(
                    path=file_path,
                    filename=row[1],
                    media_type=row[3]
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting file: {str(e)}")

@app.get("/workspaces/{workspace_id}/files")
async def get_workspace_files(workspace_id: str):
    """Get all files in a workspace"""
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT id, filename, original_name, file_size, mime_type, created_at
                    FROM files WHERE workspace_id = ? ORDER BY created_at DESC
                """, (workspace_id,))
                
                files = []
                for row in cursor.fetchall():
                    files.append({
                        "id": row[0],
                        "filename": row[1],
                        "original_name": row[2],
                        "file_size": row[3],
                        "mime_type": row[4],
                        "created_at": row[5]
                    })
                
                return files
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting workspace files: {str(e)}")

@app.post("/ai/analyze-file/{file_id}")
async def analyze_file_with_ai(file_id: str):
    """Analyze a file using AI"""
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=400, detail="Google API key not configured")
    
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT original_name, file_path, mime_type, file_size
                    FROM files WHERE id = ?
                """, (file_id,))
                row = cursor.fetchone()
                
                if not row:
                    raise HTTPException(status_code=404, detail="File not found")
                
                filename, file_path, mime_type, file_size = row
                
                # Analyze based on file type
                analysis_prompt = f"""
                Analyze this file and provide insights:
                - Filename: {filename}
                - Type: {mime_type}
                - Size: {file_size} bytes
                
                Please provide:
                1. What type of content this likely contains
                2. Potential use cases
                3. Suggestions for organization or related content
                4. Any notable characteristics
                
                Keep the analysis concise but informative.
                """
                
                # For text files, read content for deeper analysis
                if mime_type and ('text' in mime_type or 'json' in mime_type):
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()[:2000]  # First 2000 chars
                            analysis_prompt += f"\n\nFile content preview:\n{content}"
                    except:
                        pass
                
                headers = {"Content-Type": "application/json"}
                body = {
                    "contents": [{"parts": [{"text": analysis_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.3,
                        "maxOutputTokens": 300,
                    }
                }
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                        json=body,
                        headers=headers
                    )
                    
                    if response.status_code != 200:
                        raise HTTPException(status_code=500, detail=f"AI API error: {response.text}")
                    
                    data = response.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            analysis = candidate["content"]["parts"][0].get("text", "")
                            
                            # Store analysis in database
                            conn.execute("""
                                UPDATE files SET ai_analysis = ? WHERE id = ?
                            """, (analysis, file_id))
                            conn.commit()
                            
                            return {"analysis": analysis.strip()}
                    
                    return {"analysis": "Unable to analyze file at this time."}
                    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing file: {str(e)}")

@app.post("/ai/analyze-workspace/{workspace_id}")
async def analyze_workspace_with_ai(workspace_id: str):
    """Analyze entire workspace content with AI"""
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=400, detail="Google API key not configured")
    
    try:
        with db_lock:
            with get_db() as conn:
                # Get workspace overview
                cursor = conn.execute("SELECT name, description FROM workspaces WHERE id = ?", (workspace_id,))
                workspace_row = cursor.fetchone()
                
                if not workspace_row:
                    raise HTTPException(status_code=404, detail="Workspace not found")
                
                workspace_name, workspace_desc = workspace_row
                
                # Get counts and recent items
                cursor = conn.execute("SELECT COUNT(*) FROM pages WHERE workspace_id = ? AND is_archived = FALSE", (workspace_id,))
                pages_count = cursor.fetchone()[0]
                
                cursor = conn.execute("SELECT COUNT(*) FROM databases WHERE workspace_id = ?", (workspace_id,))
                databases_count = cursor.fetchone()[0]
                
                cursor = conn.execute("SELECT COUNT(*) FROM files WHERE workspace_id = ?", (workspace_id,))
                files_count = cursor.fetchone()[0]
                
                # Get recent page titles
                cursor = conn.execute("""
                    SELECT title FROM pages WHERE workspace_id = ? AND is_archived = FALSE 
                    ORDER BY updated_at DESC LIMIT 5
                """, (workspace_id,))
                recent_pages = [row[0] for row in cursor.fetchall()]
                
                analysis_prompt = f"""
                Analyze this workspace and provide insights:
                
                Workspace: {workspace_name}
                Description: {workspace_desc or 'No description'}
                
                Content Overview:
                - {pages_count} pages
                - {databases_count} databases  
                - {files_count} files
                
                Recent pages: {', '.join(recent_pages) if recent_pages else 'None'}
                
                Please provide:
                1. Overall workspace purpose and theme
                2. Content organization assessment
                3. Productivity insights and suggestions
                4. Recommendations for improvement
                5. Missing content types that might be useful
                
                Keep the analysis comprehensive but concise.
                """
                
                headers = {"Content-Type": "application/json"}
                body = {
                    "contents": [{"parts": [{"text": analysis_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.4,
                        "maxOutputTokens": 500,
                    }
                }
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                        json=body,
                        headers=headers
                    )
                    
                    if response.status_code != 200:
                        raise HTTPException(status_code=500, detail=f"AI API error: {response.text}")
                    
                    data = response.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            summary = candidate["content"]["parts"][0].get("text", "")
                            return {"summary": summary.strip()}
                    
                    return {"summary": "Unable to analyze workspace at this time."}
                    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing workspace: {str(e)}")

# ============================================================================
# LEGACY CALENDAR ENDPOINTS (for backward compatibility)
# ============================================================================

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

# ============================================================================
# AI-POWERED FEATURES
# ============================================================================

@app.get("/ai/test")
async def test_ai():
    """Test AI configuration"""
    return {
        "api_key_configured": bool(GOOGLE_API_KEY),
        "api_key_length": len(GOOGLE_API_KEY) if GOOGLE_API_KEY else 0,
        "api_key_preview": GOOGLE_API_KEY[:10] + "..." if GOOGLE_API_KEY else None
    }

@app.post("/ai/generate-content")
async def generate_content(request: Dict[str, Any]):
    """Generate AI content for pages, blocks, or summaries"""
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=400, detail="Google API key not configured")
    
    try:
        content_type = request.get("type", "text")
        prompt = request.get("prompt", "")
        context = request.get("context", {})
        
        # Build AI prompt based on content type
        if content_type == "page_summary":
            ai_prompt = f"Create a comprehensive summary for a page titled '{context.get('title', 'Untitled')}'. {prompt}"
        elif content_type == "task_breakdown":
            ai_prompt = f"Break down this task into actionable steps: {prompt}"
        elif content_type == "meeting_notes":
            ai_prompt = f"Structure these meeting notes with key points, action items, and decisions: {prompt}"
        elif content_type == "project_plan":
            ai_prompt = f"Create a project plan with phases, milestones, and deliverables for: {prompt}"
        else:
            ai_prompt = prompt
        
        # Call Gemini API
        headers = {"Content-Type": "application/json"}
        body = {
            "contents": [{"parts": [{"text": ai_prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "topK": 1,
                "topP": 1,
                "maxOutputTokens": 1000,
            }
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                json=body,
                headers=headers
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail=f"AI API error: {response.text}")
            
            data = response.json()
            if "candidates" in data and len(data["candidates"]) > 0:
                candidate = data["candidates"][0]
                if "content" in candidate and "parts" in candidate["content"]:
                    generated_text = candidate["content"]["parts"][0].get("text", "")
                    return {"content": generated_text.strip(), "type": content_type}
            
            return {"content": "Unable to generate content at this time.", "type": content_type}
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating AI content: {str(e)}")

@app.post("/ai/improve-writing")
async def improve_writing(request: Dict[str, str]):
    """Improve writing quality of text"""
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=400, detail="Google API key not configured")
    
    try:
        text = request.get("text", "")
        improvement_type = request.get("type", "general")  # general, formal, casual, concise
        
        prompts = {
            "general": f"Improve the clarity and readability of this text while maintaining its meaning: {text}",
            "formal": f"Rewrite this text in a more formal, professional tone: {text}",
            "casual": f"Rewrite this text in a more casual, friendly tone: {text}",
            "concise": f"Make this text more concise while keeping all important information: {text}",
            "grammar": f"Fix grammar, spelling, and punctuation errors in this text: {text}"
        }
        
        prompt = prompts.get(improvement_type, prompts["general"])
        
        headers = {"Content-Type": "application/json"}
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 500,
            }
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                json=body,
                headers=headers
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail=f"AI API error: {response.text}")
            
            data = response.json()
            if "candidates" in data and len(data["candidates"]) > 0:
                candidate = data["candidates"][0]
                if "content" in candidate and "parts" in candidate["content"]:
                    improved_text = candidate["content"]["parts"][0].get("text", "")
                    return {"improved_text": improved_text.strip(), "original_text": text}
            
            return {"improved_text": text, "original_text": text}
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error improving writing: {str(e)}")

# ============================================================================
# TEMPLATE ENDPOINTS
# ============================================================================

@app.get("/templates", response_model=List[TemplateResponse])
async def get_templates(category: Optional[str] = Query(None)):
    """Get all templates, optionally filtered by category"""
    try:
        with db_lock:
            with get_db() as conn:
                if category:
                    cursor = conn.execute("""
                        SELECT id, name, description, category, template_data, created_at
                        FROM templates WHERE category = ? ORDER BY name
                    """, (category,))
                else:
                    cursor = conn.execute("""
                        SELECT id, name, description, category, template_data, created_at
                        FROM templates ORDER BY category, name
                    """)
                
                templates = []
                for row in cursor.fetchall():
                    templates.append({
                        "id": row[0],
                        "name": row[1],
                        "description": row[2],
                        "category": row[3],
                        "template_data": json.loads(row[4]) if row[4] else {},
                        "created_at": row[5]
                    })
                return templates
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching templates: {str(e)}")

@app.post("/templates", response_model=TemplateResponse)
async def create_template(template: Template):
    """Create a new template"""
    try:
        template_id = str(uuid.uuid4())
        with db_lock:
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO templates (id, name, description, category, template_data) 
                    VALUES (?, ?, ?, ?, ?)
                """, (template_id, template.name, template.description, template.category,
                      json.dumps(template.template_data)))
                conn.commit()
                
                # Get the created template
                cursor = conn.execute("""
                    SELECT id, name, description, category, template_data, created_at
                    FROM templates WHERE id = ?
                """, (template_id,))
                row = cursor.fetchone()
                
                return {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "category": row[3],
                    "template_data": json.loads(row[4]) if row[4] else {},
                    "created_at": row[5]
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating template: {str(e)}")

@app.post("/templates/{template_id}/apply")
async def apply_template(template_id: str, target: Dict[str, str]):
    """Apply a template to create new content"""
    try:
        workspace_id = target.get("workspace_id")
        if not workspace_id:
            raise HTTPException(status_code=400, detail="workspace_id required")
        
        with db_lock:
            with get_db() as conn:
                # Get template
                cursor = conn.execute("""
                    SELECT name, template_data FROM templates WHERE id = ?
                """, (template_id,))
                row = cursor.fetchone()
                
                if not row:
                    raise HTTPException(status_code=404, detail="Template not found")
                
                template_name = row[0]
                template_data = json.loads(row[1])
                
                # Create page from template
                page_id = str(uuid.uuid4())
                page_title = target.get("title", f"{template_name} - {datetime.now().strftime('%Y-%m-%d')}")
                
                conn.execute("""
                    INSERT INTO pages (id, workspace_id, title, icon, content, page_type, properties) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (page_id, workspace_id, page_title, 
                      template_data.get("icon", "📄"),
                      json.dumps(template_data.get("content", [])),
                      template_data.get("page_type", "page"),
                      json.dumps(template_data.get("properties", {}))))
                
                # Create blocks from template
                for block_data in template_data.get("blocks", []):
                    block_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO blocks (id, page_id, type, content, position) 
                        VALUES (?, ?, ?, ?, ?)
                    """, (block_id, page_id, block_data.get("type", "paragraph"),
                          json.dumps(block_data.get("content", {})),
                          block_data.get("position", 0)))
                
                conn.commit()
                
                return {"page_id": page_id, "message": "Template applied successfully"}
                
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error applying template: {str(e)}")

# ============================================================================
# SEARCH AND ANALYTICS
# ============================================================================

@app.get("/search")
async def search_content(
    workspace_id: str = Query(...),
    query: str = Query(...),
    content_type: Optional[str] = Query(None)  # pages, databases, files
):
    """Search across all content in a workspace with AI enhancement"""
    try:
        results = {"pages": [], "databases": [], "records": [], "files": []}
        search_types = content_type.split(',') if content_type else ['pages', 'databases', 'files']
        
        with db_lock:
            with get_db() as conn:
                # Search pages
                if 'pages' in search_types:
                    cursor = conn.execute("""
                        SELECT id, title, icon, page_type, content, created_at, updated_at
                        FROM pages 
                        WHERE workspace_id = ? AND is_archived = FALSE 
                        AND (title LIKE ? OR content LIKE ?)
                        ORDER BY updated_at DESC LIMIT 20
                    """, (workspace_id, f"%{query}%", f"%{query}%"))
                    
                    for row in cursor.fetchall():
                        content = json.loads(row[4]) if row[4] else []
                        content_text = ' '.join([block.get('content', {}).get('text', '') for block in content if isinstance(block, dict)])
                        
                        results["pages"].append({
                            "id": row[0],
                            "title": row[1],
                            "icon": row[2],
                            "type": row[3],
                            "content": content_text[:200] + '...' if len(content_text) > 200 else content_text,
                            "created_at": row[5],
                            "updated_at": row[6]
                        })
                
                # Search databases
                if 'databases' in search_types:
                    cursor = conn.execute("""
                        SELECT id, name, icon, description, created_at, updated_at
                        FROM databases 
                        WHERE workspace_id = ? AND (name LIKE ? OR description LIKE ?)
                        ORDER BY updated_at DESC LIMIT 20
                    """, (workspace_id, f"%{query}%", f"%{query}%"))
                    
                    for row in cursor.fetchall():
                        results["databases"].append({
                            "id": row[0],
                            "name": row[1],
                            "icon": row[2],
                            "description": row[3] or 'No description',
                            "created_at": row[4],
                            "updated_at": row[5]
                        })
                
                # Search files
                if 'files' in search_types:
                    cursor = conn.execute("""
                        SELECT id, filename, original_name, mime_type, ai_analysis, created_at
                        FROM files 
                        WHERE workspace_id = ? AND (filename LIKE ? OR original_name LIKE ? OR ai_analysis LIKE ?)
                        ORDER BY created_at DESC LIMIT 20
                    """, (workspace_id, f"%{query}%", f"%{query}%", f"%{query}%"))
                    
                    for row in cursor.fetchall():
                        results["files"].append({
                            "id": row[0],
                            "filename": row[1],
                            "original_name": row[2],
                            "mime_type": row[3],
                            "description": row[4] or 'No AI analysis yet',
                            "created_at": row[5]
                        })
        
        return results
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching content: {str(e)}")

@app.post("/ai/enhance-page")
async def enhance_page_with_ai(request: Dict[str, Any]):
    """Enhance a page with AI suggestions"""
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=400, detail="Google API key not configured")
    
    try:
        page_id = request.get("page_id")
        enhancement_type = request.get("type", "general")  # general, structure, content, formatting
        
        with db_lock:
            with get_db() as conn:
                cursor = conn.execute("""
                    SELECT title, content, page_type FROM pages WHERE id = ?
                """, (page_id,))
                row = cursor.fetchone()
                
                if not row:
                    raise HTTPException(status_code=404, detail="Page not found")
                
                title, content_json, page_type = row
                content = json.loads(content_json) if content_json else []
                
                # Extract text content
                text_content = []
                for block in content:
                    if isinstance(block, dict) and 'content' in block:
                        block_content = block['content']
                        if isinstance(block_content, dict) and 'text' in block_content:
                            text_content.append(block_content['text'])
                
                current_text = '\n'.join(text_content)
                
                enhancement_prompts = {
                    "general": f"Improve this page content while maintaining its structure and meaning:\n\nTitle: {title}\nContent:\n{current_text}",
                    "structure": f"Suggest better organization and structure for this page:\n\nTitle: {title}\nContent:\n{current_text}",
                    "content": f"Suggest additional content and sections that would make this page more comprehensive:\n\nTitle: {title}\nContent:\n{current_text}",
                    "formatting": f"Suggest better formatting, headings, and visual organization for this page:\n\nTitle: {title}\nContent:\n{current_text}"
                }
                
                prompt = enhancement_prompts.get(enhancement_type, enhancement_prompts["general"])
                
                headers = {"Content-Type": "application/json"}
                body = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.4,
                        "maxOutputTokens": 800,
                    }
                }
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{GEMINI_API_URL}?key={GOOGLE_API_KEY}",
                        json=body,
                        headers=headers
                    )
                    
                    if response.status_code != 200:
                        raise HTTPException(status_code=500, detail=f"AI API error: {response.text}")
                    
                    data = response.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            suggestions = candidate["content"]["parts"][0].get("text", "")
                            return {"suggestions": suggestions.strip(), "type": enhancement_type}
                    
                    return {"suggestions": "Unable to generate suggestions at this time.", "type": enhancement_type}
                    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error enhancing page: {str(e)}")

@app.get("/analytics/{workspace_id}")
async def get_workspace_analytics(workspace_id: str):
    """Get analytics for a workspace"""
    try:
        with db_lock:
            with get_db() as conn:
                # Basic counts
                cursor = conn.execute("SELECT COUNT(*) FROM pages WHERE workspace_id = ? AND is_archived = FALSE", (workspace_id,))
                pages_count = cursor.fetchone()[0]
                
                cursor = conn.execute("SELECT COUNT(*) FROM databases WHERE workspace_id = ?", (workspace_id,))
                databases_count = cursor.fetchone()[0]
                
                cursor = conn.execute("SELECT COUNT(*) FROM files WHERE workspace_id = ?", (workspace_id,))
                files_count = cursor.fetchone()[0]
                
                # Recent activity
                cursor = conn.execute("""
                    SELECT 'page' as type, title as name, updated_at 
                    FROM pages WHERE workspace_id = ? AND is_archived = FALSE
                    UNION ALL
                    SELECT 'database' as type, name, updated_at 
                    FROM databases WHERE workspace_id = ?
                    ORDER BY updated_at DESC LIMIT 10
                """, (workspace_id, workspace_id))
                
                recent_activity = []
                for row in cursor.fetchall():
                    recent_activity.append({
                        "type": row[0],
                        "name": row[1],
                        "updated_at": row[2]
                    })
                
                return {
                    "pages_count": pages_count,
                    "databases_count": databases_count,
                    "files_count": files_count,
                    "recent_activity": recent_activity
                }
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting analytics: {str(e)}")

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
            "api_key_length": len(GOOGLE_API_KEY) if GOOGLE_API_KEY else 0,
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

# API key is already loaded above

# ============================================================================
# ADDITIONAL UTILITY ENDPOINTS
# ============================================================================

@app.get("/workspaces/{workspace_id}/export")
async def export_workspace(workspace_id: str):
    """Export entire workspace as JSON"""
    try:
        with db_lock:
            with get_db() as conn:
                # Get workspace info
                cursor = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
                workspace_row = cursor.fetchone()
                if not workspace_row:
                    raise HTTPException(status_code=404, detail="Workspace not found")
                
                # Get all pages
                cursor = conn.execute("SELECT * FROM pages WHERE workspace_id = ?", (workspace_id,))
                pages = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
                
                # Get all databases
                cursor = conn.execute("SELECT * FROM databases WHERE workspace_id = ?", (workspace_id,))
                databases = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
                
                # Get all files
                cursor = conn.execute("SELECT * FROM files WHERE workspace_id = ?", (workspace_id,))
                files = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
                
                export_data = {
                    "workspace": dict(zip([col[0] for col in cursor.description], workspace_row)),
                    "pages": pages,
                    "databases": databases,
                    "files": files,
                    "export_date": datetime.now().isoformat(),
                    "version": "1.0"
                }
                
                return export_data
                
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error exporting workspace: {str(e)}")

@app.post("/workspaces/{workspace_id}/duplicate")
async def duplicate_workspace(workspace_id: str, new_name: str):
    """Duplicate a workspace with all its content"""
    try:
        with db_lock:
            with get_db() as conn:
                # Get original workspace
                cursor = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
                workspace_row = cursor.fetchone()
                if not workspace_row:
                    raise HTTPException(status_code=404, detail="Workspace not found")
                
                # Create new workspace
                new_workspace_id = str(uuid.uuid4())
                conn.execute("""
                    INSERT INTO workspaces (id, name, description, icon) 
                    VALUES (?, ?, ?, ?)
                """, (new_workspace_id, new_name, workspace_row[2], workspace_row[3]))
                
                # Duplicate pages
                cursor = conn.execute("SELECT * FROM pages WHERE workspace_id = ?", (workspace_id,))
                for page_row in cursor.fetchall():
                    new_page_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO pages (id, workspace_id, parent_id, title, icon, cover_url, 
                                         content, page_type, properties, is_template, is_archived) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (new_page_id, new_workspace_id, page_row[2], page_row[3], page_row[4],
                          page_row[5], page_row[6], page_row[7], page_row[8], page_row[9], page_row[10]))
                
                # Duplicate databases
                cursor = conn.execute("SELECT * FROM databases WHERE workspace_id = ?", (workspace_id,))
                for db_row in cursor.fetchall():
                    new_db_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO databases (id, workspace_id, name, description, icon, schema, view_config) 
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (new_db_id, new_workspace_id, db_row[2], db_row[3], db_row[4], db_row[5], db_row[6]))
                
                conn.commit()
                
                return {"message": "Workspace duplicated successfully", "new_workspace_id": new_workspace_id}
                
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error duplicating workspace: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting AI Workspace server...")
    print("🏠 Notion-like workspace with comprehensive features")
    print("📄 Pages, databases, blocks, and file management")
    print("🤖 AI-powered content generation and assistance")
    print("📱 Mobile-optimized with offline support")
    print("🔄 Cross-device synchronization enabled")
    print("💾 SQLite database for data persistence")
    print("📁 File upload and management")
    print("📋 Built-in templates and productivity tools")
    
    # Check API key status
    if GOOGLE_API_KEY:
        print(f"🤖 Google AI configured (Key: {GOOGLE_API_KEY[:8]}...)")
    else:
        print("⚠️  GOOGLE_API_KEY not found!")
        print("   Option 1: Set with command: GOOGLE_API_KEY=your_key python main.py")
        print("   Option 2: Create .env file with: GOOGLE_API_KEY=your_key")
        print("   Option 3: Export in terminal: export GOOGLE_API_KEY=your_key")
    
    print("🌐 Access the workspace at: http://localhost:8000")
    print("📅 Access the calendar at: http://localhost:8000/calendar")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)