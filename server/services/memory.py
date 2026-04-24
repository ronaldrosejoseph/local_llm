"""
Hybrid Memory System — three-layer context assembly for LLM prompts.

Layer 1: Rolling Window  — last N turns (token-budget aware)
Layer 2: Summary         — progressive compression of older turns
Layer 3: Vector Memory   — semantic retrieval across all chats

The context assembly pipeline fills the model's context window intelligently:
  [system prompt] → [summary] → [retrieved memories] → [rolling window] → [current message]
"""

import json
import threading
import numpy as np

from contextlib import closing

from server import state
from server.db import get_db_connection
from server.config import load_config
from server.services.rag import get_embedder


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_tokens(text: str) -> int:
    """Count tokens using the currently loaded tokenizer.
    
    Falls back to a word-based heuristic (~1.3 tokens/word) when the
    tokenizer is unavailable.
    """
    if state.tokenizer is not None:
        try:
            return len(state.tokenizer.encode(text))
        except Exception:
            pass
    # Rough heuristic fallback
    return int(len(text.split()) * 1.3)


def count_message_tokens(messages: list[dict]) -> int:
    """Count total tokens for a list of chat messages.
    
    Accounts for chat-template overhead (~4 tokens per message for role
    tags, delimiters, etc.).
    """
    total = 0
    for msg in messages:
        total += count_tokens(msg["content"]) + 4  # role tags overhead
    return total


# ---------------------------------------------------------------------------
# Message embedding (for vector memory)
# ---------------------------------------------------------------------------

def embed_message_pair(user_content: str, assistant_content: str) -> np.ndarray | None:
    """Embed a user+assistant turn pair for semantic retrieval.
    
    We concatenate both messages so the embedding captures the full
    semantic context of the exchange (a standalone answer is meaningless
    without its question).
    """
    embedder = get_embedder()
    if embedder is None:
        return None

    combined = f"User: {user_content}\nAssistant: {assistant_content}"
    # Truncate to embedder's max sequence length (~256 tokens for MiniLM)
    combined = combined[:2000]
    try:
        return embedder.encode([combined])[0]
    except Exception as e:
        print(f"Memory: Failed to embed message pair: {e}")
        return None


def save_message_embedding(message_id: int, embedding: np.ndarray):
    """Persist an embedding blob to an existing messages row."""
    blob = embedding.tobytes()
    with closing(get_db_connection()) as conn:
        conn.execute(
            "UPDATE messages SET embedding = ? WHERE id = ?",
            (blob, message_id)
        )
        conn.commit()


def embed_and_save_turn(chat_id: str, user_content: str, assistant_content: str, assistant_msg_id: int):
    """Compute and store embedding for the latest turn pair.
    
    Called asynchronously after generation completes.
    """
    emb = embed_message_pair(user_content, assistant_content)
    if emb is not None:
        save_message_embedding(assistant_msg_id, emb)
        print(f"Memory: Embedded turn pair for message {assistant_msg_id}")


# ---------------------------------------------------------------------------
# Vector retrieval (cross-chat)
# ---------------------------------------------------------------------------

# Minimum cosine similarity to consider a memory "relevant"
MEMORY_SIMILARITY_THRESHOLD = 0.55


def retrieve_relevant_memories(query: str, current_chat_id: str,
                                top_k: int = 5, max_tokens: int = 500) -> list[dict]:
    """Search all chats for semantically relevant past turn-pairs.
    
    Returns a list of dicts: [{chat_title, role, content, similarity}, ...]
    Excludes messages from the current chat's rolling window (dedup handled by caller).
    """
    embedder = get_embedder()
    if embedder is None:
        return []

    try:
        query_emb = embedder.encode([query[:2000]])[0]
    except Exception as e:
        print(f"Memory: Failed to encode query: {e}")
        return []

    # Fetch all messages that have embeddings (across all chats)
    with closing(get_db_connection()) as conn:
        rows = conn.execute("""
            SELECT m.id, m.chat_id, m.role, m.content, m.embedding,
                   c.title AS chat_title
            FROM messages m
            JOIN chats c ON m.chat_id = c.id
            WHERE m.embedding IS NOT NULL
            ORDER BY m.timestamp DESC
        """).fetchall()

    if not rows:
        return []

    # Decode embeddings and compute cosine similarity
    candidates = []
    for row in rows:
        try:
            emb = np.frombuffer(row["embedding"], dtype=np.float32).copy()
            candidates.append({
                "id": row["id"],
                "chat_id": row["chat_id"],
                "chat_title": row["chat_title"],
                "role": row["role"],
                "content": row["content"],
                "emb": emb,
            })
        except Exception:
            continue

    if not candidates:
        return []

    emb_matrix = np.array([c["emb"] for c in candidates])
    q_norm = query_emb / (np.linalg.norm(query_emb) + 1e-10)
    d_norms = emb_matrix / (np.linalg.norm(emb_matrix, axis=1, keepdims=True) + 1e-10)
    similarities = np.dot(d_norms, q_norm)

    # Rank and select top-k
    top_indices = np.argsort(similarities)[::-1]

    results = []
    token_budget = max_tokens
    seen_msg_ids = set()

    for idx in top_indices:
        if len(results) >= top_k:
            break

        candidate = candidates[idx]
        sim_score = float(similarities[idx])

        # Skip memories below relevance threshold
        if sim_score < MEMORY_SIMILARITY_THRESHOLD:
            break  # Sorted descending, so all remaining are worse

        if candidate["id"] in seen_msg_ids:
            continue

        # Get the paired user message for this assistant response
        # (We store embeddings on the assistant message of each turn)
        pair_content = candidate["content"]
        pair_tokens = count_tokens(pair_content)
        if pair_tokens > token_budget:
            continue

        results.append({
            "id": candidate["id"],
            "chat_id": candidate["chat_id"],
            "chat_title": candidate["chat_title"],
            "role": candidate["role"],
            "content": candidate["content"],
            "similarity": float(similarities[idx]),
        })
        seen_msg_ids.add(candidate["id"])
        token_budget -= pair_tokens

    if results:
        print(f"Memory: Retrieved {len(results)} memories "
              f"(sim range: {results[0]['similarity']:.3f}–{results[-1]['similarity']:.3f})")
    return results


# ---------------------------------------------------------------------------
# Context assembly (the core pipeline)
# ---------------------------------------------------------------------------

def assemble_context(chat_id: str, current_message: str, system_prompt: str,
                      rag_context: str = "", web_context: str = "") -> list[dict]:
    """Build the messages list for the model using the hybrid memory system.
    
    Priority allocation (highest → lowest):
      1. System prompt          — always included
      2. Current user message   — always included  
      3. Generation headroom    — reserved (max_tokens from config)
      4. Rolling window         — flex layer, fills remaining budget
      5. Chat summary           — included if exists
      6. Retrieved memories     — semantic search across chats
      7. RAG/Web context        — injected into current message
    
    Returns the final messages list ready for chat template formatting.
    """
    cfg = load_config()
    max_gen_tokens = cfg["max_tokens"]
    
    # Determine total context window from model config
    context_window = _get_model_context_length()

    # Safety: cap generation headroom so input always gets at least 25% of context
    gen_headroom = min(max_gen_tokens, int(context_window * 0.75))
    
    memory_cfg = {
        "memory_top_k": cfg.get("memory_top_k", 5),
        "memory_max_tokens": cfg.get("memory_max_tokens", 600),
        "summary_max_tokens": cfg.get("summary_max_tokens", 400),
    }

    # --- Fixed allocations ---
    messages = []
    used_tokens = 0

    # 1. System prompt (always)
    if system_prompt:
        system_tokens = count_tokens(system_prompt) + 4
        used_tokens += system_tokens

    # 2. Reserve generation headroom
    used_tokens += gen_headroom

    # 3. Current user message (with RAG/Web injected)
    augmented_message = current_message
    combined_context = web_context + ("\n" if web_context and rag_context else "") + rag_context
    if combined_context:
        augmented_message = f"{combined_context}\nInstructions: Utilizing the context provided above, answer the following query:\n\n{current_message.replace('/web', '').strip()}"

    current_msg_tokens = count_tokens(augmented_message) + 4
    used_tokens += current_msg_tokens

    # --- Flexible allocations (fill the remaining budget) ---
    remaining_budget = max(0, context_window - used_tokens)

    print(f"Memory: context_window={context_window}, gen_headroom={gen_headroom}, "
          f"system={used_tokens - gen_headroom - current_msg_tokens}, "
          f"current_msg={current_msg_tokens}, remaining_budget={remaining_budget}")

    # 4. Load chat summary
    summary = ""
    summary_tokens = 0
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT summary FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if row and row["summary"]:
            summary = row["summary"]
            summary_tokens = count_tokens(summary) + 20  # overhead for "CONVERSATION SUMMARY:" label
            if summary_tokens <= remaining_budget and summary_tokens <= memory_cfg["summary_max_tokens"] + 50:
                remaining_budget -= summary_tokens
            else:
                summary = ""  # Too large, skip
                summary_tokens = 0

    # 5. Reserve budget for vector memories (will fill later)
    memory_budget = min(memory_cfg["memory_max_tokens"], remaining_budget // 3)
    remaining_budget -= memory_budget

    # 6. Fill rolling window with remaining budget (newest messages first)
    rolling_window = _build_rolling_window(chat_id, remaining_budget)
    rolling_msg_ids = {m["id"] for m in rolling_window if "id" in m}

    # 7. Retrieve vector memories (cross-chat, deduplicated)
    #    Skip on the very first message of a new chat — no established topic yet.
    retrieved_memories = []
    is_first_message = len(rolling_window) == 0
    if memory_budget > 50 and not is_first_message:
        raw_memories = retrieve_relevant_memories(
            current_message, chat_id,
            top_k=memory_cfg["memory_top_k"],
            max_tokens=memory_budget
        )
        # Deduplicate: exclude messages already in the rolling window
        retrieved_memories = [m for m in raw_memories if m["id"] not in rolling_msg_ids]

    # --- Assemble final messages list ---

    # System prompt + summary + memories as system context
    system_content = system_prompt or ""

    if summary:
        system_content += f"\n\nCONVERSATION SUMMARY (earlier context):\n{summary}"

    if retrieved_memories:
        memory_text = "\n\nRELEVANT PAST EXCHANGES:\n"
        for mem in retrieved_memories:
            source = f"[from: {mem['chat_title']}]" if mem["chat_id"] != chat_id else ""
            memory_text += f"- {source} {mem['content'][:500]}\n"
        system_content += memory_text

    if system_content.strip():
        messages.append({"role": "system", "content": system_content.strip()})

    # Rolling window messages (already in chronological order)
    for msg in rolling_window:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # Current user message (with RAG/web context injected)
    messages.append({"role": "user", "content": augmented_message})

    return messages


def _get_model_context_length() -> int:
    """Get the current model's context window size.
    
    Checks the model config for max_position_embeddings or similar fields,
    including nested configs (e.g. text_config for VLM models like Gemma 4).
    Also checks the tokenizer's model_max_length as a fallback.
    Falls back to 131072 if nothing is detected (safe for modern models).
    """
    context_keys = ["max_position_embeddings", "max_seq_len", "context_length",
                    "max_sequence_length", "n_positions", "seq_length"]
    # Sub-configs that may hold context length for VLM/multimodal models
    nested_keys = ["text_config", "language_config", "llm_config"]

    def _search_config(config_obj, prefix=""):
        """Search a config object (dataclass or dict) for context length keys."""
        if config_obj is None:
            return None

        for key in context_keys:
            # Attribute access (dataclass / namespace)
            val = getattr(config_obj, key, None)
            if val and isinstance(val, int) and val > 0:
                print(f"Memory: Detected context window = {val} (from {prefix}{key})")
                return val
            # Dict access
            if isinstance(config_obj, dict) and key in config_obj:
                val = config_obj[key]
                if val and isinstance(val, int) and val > 0:
                    print(f"Memory: Detected context window = {val} (from {prefix}{key})")
                    return val

        # Recurse into nested sub-configs (e.g. text_config for VLMs)
        for nk in nested_keys:
            sub = getattr(config_obj, nk, None)
            if sub is None and isinstance(config_obj, dict):
                sub = config_obj.get(nk)
            if sub is not None:
                result = _search_config(sub, prefix=f"{prefix}{nk}.")
                if result:
                    return result
        return None

    try:
        if state.model is not None:
            # Check model.config, model.args, and model.config.text_config etc.
            for attr in ["config", "args"]:
                config_obj = getattr(state.model, attr, None)
                if config_obj is not None:
                    result = _search_config(config_obj, prefix=f"model.{attr}.")
                    if result:
                        return result

        # Fallback: check the tokenizer's advertised max length
        if state.tokenizer is not None:
            tok_max = getattr(state.tokenizer, "model_max_length", None)
            if tok_max and isinstance(tok_max, int) and 1024 < tok_max < 10_000_000:
                print(f"Memory: Detected context window = {tok_max} (from tokenizer.model_max_length)")
                return tok_max

    except Exception as e:
        print(f"Memory: Could not detect context length: {e}")

    # Modern models typically support large context windows
    print("Memory: Using default context window = 131072")
    return 131072


def _build_rolling_window(chat_id: str, token_budget: int) -> list[dict]:
    """Fetch recent messages for the rolling window, newest first, up to token budget.
    
    Returns messages in chronological order (oldest first) for proper
    conversation flow. The current user message is NOT included (it's 
    already saved to DB but will be appended separately with context).
    """
    with closing(get_db_connection()) as conn:
        # Fetch all messages in reverse chronological order
        # Exclude the very last message (the current user message we just saved)
        rows = conn.execute("""
            SELECT id, role, content FROM messages
            WHERE chat_id = ?
            ORDER BY timestamp DESC
        """, (chat_id,)).fetchall()

    if not rows:
        return []

    # Skip the first row — it's the current user message we just inserted
    rows = rows[1:]

    # Fill from newest to oldest until budget is exhausted
    selected = []
    remaining = token_budget
    for row in rows:
        msg_tokens = count_tokens(row["content"]) + 4
        if msg_tokens > remaining:
            break  # Can't fit this message, stop
        selected.append({
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
        })
        remaining -= msg_tokens

    # Reverse to chronological order
    selected.reverse()
    return selected


# ---------------------------------------------------------------------------
# Progressive summarization (async, post-generation)
# ---------------------------------------------------------------------------

def maybe_update_summary(chat_id: str):
    """Check if messages have fallen out of the rolling window and update the summary.
    
    This runs asynchronously after generation. It:
    1. Determines which messages are in the current rolling window
    2. Checks which messages are older than the window AND not yet summarized
    3. Folds those messages into the existing summary using the LLM
    """
    cfg = load_config()
    context_window = _get_model_context_length()
    max_gen_tokens = cfg["max_tokens"]
    gen_headroom = min(max_gen_tokens, int(context_window * 0.75))

    # Estimate a rough rolling window budget (conservative — no RAG/web)
    approx_window_budget = max(0, context_window - gen_headroom - 500)  # 500 for system/summary overhead

    with closing(get_db_connection()) as conn:
        # Get the summary watermark
        chat_row = conn.execute(
            "SELECT summary, summary_through_msg_id FROM chats WHERE id = ?",
            (chat_id,)
        ).fetchone()

        if not chat_row:
            return

        current_summary = chat_row["summary"] or ""
        summary_watermark = chat_row["summary_through_msg_id"] or 0

        # Get all messages for this chat
        all_msgs = conn.execute(
            "SELECT id, role, content FROM messages WHERE chat_id = ? ORDER BY timestamp ASC",
            (chat_id,)
        ).fetchall()

    if len(all_msgs) < 6:
        # Too few messages to bother summarizing
        return

    # Figure out which messages are in the rolling window
    # (same logic as _build_rolling_window but without the "skip last" since
    # the assistant response is now saved)
    window_ids = set()
    budget = approx_window_budget
    for msg in reversed(all_msgs):
        tokens = count_tokens(msg["content"]) + 4
        if tokens > budget:
            break
        window_ids.add(msg["id"])
        budget -= tokens

    # Messages that fell out of the window AND haven't been summarized yet
    unsummarized = [
        m for m in all_msgs
        if m["id"] not in window_ids and m["id"] > summary_watermark
    ]

    if not unsummarized:
        return  # Nothing new to summarize

    print(f"Memory: {len(unsummarized)} messages need summarization for chat {chat_id}")

    # Build the text of unsummarized messages
    new_text = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}" for m in unsummarized
    )

    # Truncate to prevent prompt explosion
    if len(new_text) > 4000:
        new_text = new_text[:4000] + "\n[...truncated...]"

    # Build summarization prompt
    if current_summary:
        summary_prompt = (
            "You are a conversation summarizer. Below is an existing summary of an ongoing "
            "conversation, followed by new messages that need to be incorporated. Update the "
            "summary to include the new information. Keep it concise (under 200 words). "
            "Preserve: key decisions, user preferences, specific facts, and topic progression.\n\n"
            f"EXISTING SUMMARY:\n{current_summary}\n\n"
            f"NEW MESSAGES TO INCORPORATE:\n{new_text}\n\n"
            "UPDATED SUMMARY:"
        )
    else:
        summary_prompt = (
            "Summarize the following conversation in under 200 words. Focus on: key topics discussed, "
            "decisions made, user preferences, and important facts or details worth remembering.\n\n"
            f"CONVERSATION:\n{new_text}\n\n"
            "SUMMARY:"
        )

    # Run summarization using the LLM (requires lock)
    if not state.generation_lock.acquire(blocking=False):
        print("Memory: Skipping summarization — model busy")
        return

    try:
        summary_messages = [{"role": "user", "content": summary_prompt}]

        if state.IS_VLM:
            from mlx_vlm import generate as generate_vlm
            from mlx_vlm.prompt_utils import apply_chat_template as apply_vlm_template

            prompt = apply_vlm_template(
                state.processor, state.vlm_config, summary_messages, num_images=0
            )
            result = generate_vlm(
                state.model, state.processor,
                prompt=prompt, max_tokens=300, verbose=False
            )
        else:
            from mlx_lm import generate
            prompt = state.tokenizer.apply_chat_template(
                summary_messages, tokenize=False, add_generation_prompt=True
            )
            result = generate(
                state.model, state.tokenizer,
                prompt=prompt, max_tokens=300, verbose=False
            )

        new_summary = result if isinstance(result, str) else getattr(result, "text", str(result))
        new_summary = new_summary.strip()

        # Persist the updated summary and watermark
        new_watermark = unsummarized[-1]["id"]
        with closing(get_db_connection()) as conn:
            conn.execute(
                "UPDATE chats SET summary = ?, summary_through_msg_id = ? WHERE id = ?",
                (new_summary, new_watermark, chat_id)
            )
            conn.commit()

        print(f"Memory: Summary updated for chat {chat_id} (watermark → {new_watermark})")

    except Exception as e:
        print(f"Memory: Summarization failed: {e}")
    finally:
        state.generation_lock.release()


def post_generation_tasks(chat_id: str, user_content: str,
                           assistant_content: str, assistant_msg_id: int):
    """Run all post-generation memory tasks in a background thread.
    
    Called after the response has been fully streamed and saved.
    Tasks: embed the turn pair, update the summary if needed.
    """
    def _run():
        try:
            # 1. Embed the turn pair
            embed_and_save_turn(chat_id, user_content, assistant_content, assistant_msg_id)

            # 2. Update summary if messages have fallen out of window
            maybe_update_summary(chat_id)
        except Exception as e:
            print(f"Memory: Post-generation tasks failed: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
