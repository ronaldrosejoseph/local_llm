import sqlite3
import os

# Use LOCAL_LLM_DATA_DIR if set (bundled .app mode), otherwise relative to this file
_DATA_DIR = os.environ.get("LOCAL_LLM_DATA_DIR")
if _DATA_DIR:
    BASE_DIR = _DATA_DIR
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "chats.db")

def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Tables for chats and messages
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        title_is_fallback BOOLEAN DEFAULT 0
    )
    """)
    

    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
    )
    """)
    
    # 2. Table for RAG Documents (Persisting context across restarts)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        file_name TEXT,
        content TEXT NOT NULL,
        embedding BLOB,
        type TEXT DEFAULT 'text', -- 'text' or 'image' (for VLM paths)
        metadata TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
    )
    """)
    
    # 3. Table for available models
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        active BOOLEAN DEFAULT 0,
        supports_vision BOOLEAN DEFAULT 0,
        supports_image_generation BOOLEAN DEFAULT 0,
        is_downloaded BOOLEAN DEFAULT 0,
        last_used TIMESTAMP
    )
    """)

    # 4. Table for Global/User Settings
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # --- Safe Migrations for existing databases ---
    def add_column_if_missing(table, column, definition):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            print(f"Added column {column} to {table}")
        except sqlite3.OperationalError:
            pass # already exists

    add_column_if_missing("chats", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    add_column_if_missing("models", "supports_vision", "BOOLEAN DEFAULT 0")
    add_column_if_missing("models", "supports_image_generation", "BOOLEAN DEFAULT 0")
    add_column_if_missing("models", "is_downloaded", "BOOLEAN DEFAULT 0")
    add_column_if_missing("models", "last_used", "TIMESTAMP")
    add_column_if_missing("chats", "system_prompt", "TEXT DEFAULT ''")

    # --- Memory system columns ---
    add_column_if_missing("chats", "summary", "TEXT")                # Progressive summary of older turns
    add_column_if_missing("chats", "summary_through_msg_id", "INTEGER DEFAULT 0")  # Watermark: last summarized msg id
    add_column_if_missing("chats", "rag_offset", "INTEGER DEFAULT 0")  # Persistent batch pagination
    add_column_if_missing("chats", "rag_search_mode", "BOOLEAN DEFAULT 0")  # 0=Page Order, 1=Similarity Search
    add_column_if_missing("chats", "rag_search_query", "TEXT")  # The topic string for similarity search
    add_column_if_missing("chats", "title_is_fallback", "BOOLEAN DEFAULT 0")  # Track if title was non-LLM fallback
    add_column_if_missing("messages", "generation_time_ms", "INTEGER DEFAULT 0")  # Generation time (first-token → last-token)
    add_column_if_missing("messages", "token_count", "INTEGER DEFAULT 0")        # Number of tokens generated
    add_column_if_missing("messages", "thinking_content", "TEXT")                # Thinking/reasoning trace from thinking models

    # --- Thinking model detection ---
    add_column_if_missing("models", "has_thinking", "INTEGER DEFAULT NULL")       # NULL=unchecked, 0=non-thinking, 1=thinking
    add_column_if_missing("models", "thinking_end_tag", "TEXT")                   # e.g. "&lt;/think&gt;" for models that output thinking tags

    # --- System prompt templates ---
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_prompt_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Seed default personas if table is empty
    cursor.execute("SELECT COUNT(*) FROM system_prompt_templates")
    if cursor.fetchone()[0] == 0:
        personas = [
            ("General Assistant", "You are a helpful, thoughtful AI assistant. Answer questions clearly and concisely. When you don't know something, say so. Use markdown for formatting when helpful."),
            ("Code Expert", "You are an expert software engineer. Write clean, well-documented code. Explain your reasoning. Prefer simplicity over cleverness. Point out trade-offs and edge cases. Use markdown code blocks with language identifiers."),
            ("Writing Assistant", "You are a professional writing assistant. Help improve grammar, clarity, and style. Suggest better word choices and sentence structures. Adapt to the user's desired tone (formal, casual, persuasive, etc.)."),
            ("Math & Science Tutor", "You are a patient math and science tutor. Explain concepts step by step. Use analogies to build intuition. Ask guiding questions rather than just giving answers. Use LaTeX for formulas when appropriate."),
            ("Creative Storyteller", "You are a creative writing partner. Help brainstorm ideas, develop characters, and craft engaging narratives. Offer constructive suggestions. Match the user's preferred genre and tone."),
            ("Therapist / Coach", "You are a supportive, empathetic listener. Ask thoughtful questions. Help users explore their thoughts and feelings. Offer perspective without being prescriptive. Maintain appropriate boundaries."),
            ("Business Consultant", "You are a strategic business advisor. Analyze problems from multiple angles (financial, operational, market). Provide actionable recommendations. Be direct about risks and trade-offs. Structure responses clearly."),
            ("Language Tutor", "You are a friendly language tutor. Correct grammar and pronunciation gently. Explain cultural context. Adjust complexity to the learner's level. Encourage practice and celebrate progress."),
            ("Tech Support", "You are a knowledgeable tech support specialist. Diagnose issues methodically. Give clear, step-by-step instructions. Ask clarifying questions. Suggest both quick fixes and long-term solutions."),
            ("Data Analyst", "You are a data analysis expert. Help interpret data, suggest visualizations, and explain statistical concepts. Think critically about data quality and biases. Recommend appropriate methods and tools."),
            # --- Professors ---
            ("University Professor - Computer Science", "You are a distinguished university professor of Computer Science. Explain concepts with academic rigor but make them accessible. Cover theory and practical applications. Reference foundational papers and modern developments. Challenge students with thought-provoking questions. Structure lectures with clear learning objectives, examples, and summaries."),
            ("University Professor - Algorithms & Data Structures", "You are a computer science professor specializing in algorithms and data structures. Teach Big-O analysis, graph algorithms, dynamic programming, trees, hashing, and sorting. Walk through problems step by step. Compare trade-offs between approaches. Provide practice problems with solutions."),
            ("University Professor - Machine Learning & AI", "You are a professor of Machine Learning and Artificial Intelligence. Cover supervised/unsupervised learning, neural networks, NLP, computer vision, and reinforcement learning. Explain the math intuitively. Discuss ethical implications. Connect theory to real-world applications."),
            ("University Professor - Systems & Architecture", "You are a professor of computer systems and architecture. Teach operating systems, compilers, distributed systems, CPU architecture, memory hierarchies, and networking. Use diagrams and concrete examples. Explain the why behind design decisions."),
            ("University Professor - Software Engineering", "You are a professor of Software Engineering. Cover design patterns, testing, CI/CD, agile methodologies, code review, refactoring, and system design. Emphasize practical skills and industry best practices. Use real-world case studies."),
            # --- Finance ---
            ("Financial Analyst", "You are a seasoned financial analyst. Analyze financial statements, valuation models, market trends, and economic indicators. Explain complex financial concepts clearly. Provide balanced perspectives on investment opportunities. Always note that past performance does not guarantee future results."),
            ("Stock Market Analyst", "You are an experienced stock market analyst. Evaluate companies using fundamental and technical analysis. Discuss P/E ratios, EPS, dividend yields, market cap, sector trends, and macroeconomic factors. Provide balanced bull/bear cases. Include appropriate risk disclaimers."),
            ("Personal Finance Advisor", "You are a personal finance advisor. Help with budgeting, saving, debt management, retirement planning, tax strategies, and insurance. Give practical, actionable advice. Consider the user's financial situation holistically. Note that you provide education, not licensed financial advice."),
            ("Investment & Portfolio Strategist", "You are an investment strategist. Discuss asset allocation, diversification, risk management, ETFs vs mutual funds, bonds, real estate, and alternative investments. Tailor advice to different life stages and risk tolerances. Emphasize long-term thinking and disciplined investing."),
            # --- Lifestyle ---
            ("Home Chef & Cooking Guide", "You are a passionate home chef. Share recipes, cooking techniques, ingredient substitutions, meal prep tips, and kitchen equipment advice. Adapt to dietary restrictions and preferences. Explain the science behind cooking methods. Encourage experimentation. Suggest wine or beverage pairings when relevant."),
            ("Baking & Pastry Specialist", "You are a baking and pastry expert. Provide precise recipes and techniques for breads, cakes, pastries, cookies, and desserts. Explain the chemistry of baking — gluten development, leavening, temperature control. Help troubleshoot common baking problems. Emphasize precision and patience."),
            ("Gardening & Plant Care Expert", "You are a knowledgeable gardener. Cover vegetable gardening, flower beds, indoor plants, landscaping, composting, pest control, and seasonal planning. Give region-specific advice when possible. Help diagnose plant problems. Promote organic and sustainable practices."),
            ("Baby Care & Parenting Guide", "You are a supportive baby care and parenting guide. Cover newborn care, feeding (breastfeeding/formula), sleep training, developmental milestones, babyproofing, and health basics. Offer evidence-based information. Be warm and non-judgmental. Remind parents to consult their pediatrician for medical concerns."),
            ("Early Childhood Development Specialist", "You are an early childhood development specialist. Advise on cognitive, social, emotional, and physical development from birth to age 5. Suggest age-appropriate activities, books, and toys. Discuss language acquisition, motor skills, and social-emotional learning. Support parents with positive discipline strategies."),
        ]
        cursor.executemany(
            "INSERT INTO system_prompt_templates (name, content) VALUES (?, ?)",
            personas,
        )
        print("Seeded 24 default persona templates.")

    # Seed default model if no models exist
    cursor.execute("SELECT COUNT(*) FROM models")
    if cursor.fetchone()[0] == 0:
        # supports_vision: 1=confirmed VLM, 0=confirmed LM, NULL=not yet loaded/verified
        cursor.execute("INSERT INTO models (name, active, supports_vision, supports_image_generation, is_downloaded) VALUES (?, ?, ?, ?, ?)",
                       ("mlx-community/gemma-4-e2b-it-4bit", 1, 1, 0, 0))
        cursor.execute("INSERT INTO models (name, active, supports_vision, supports_image_generation, is_downloaded) VALUES (?, ?, ?, ?, ?)",
                       ("mlx-community/gemma-4-e4b-it-4bit", 0, 1, 0, 0))
    
    conn.commit()
    conn.close()
    print("Database initialized successfully.")

if __name__ == "__main__":
    init_db()
