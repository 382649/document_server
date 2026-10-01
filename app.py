"""AI Document Vault Agent - Streamlit UI."""
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import streamlit as st  # noqa: E402

import agent  # noqa: E402
import auth  # noqa: E402
import document_manager as dm  # noqa: E402
from database import DatabaseError, init_db  # noqa: E402

st.set_page_config(page_title="AI Document Vault", page_icon="🗂️", layout="wide")

LIST_LIMIT = 25


# ------------------------------------------------------------------ helpers
def init_state():
    ss = st.session_state
    ss.setdefault("user", None)
    ss.setdefault("messages", [])
    ss.setdefault("uploader_key", 0)
    ss.setdefault("confirm_delete", None)
    ss.setdefault("flash", [])


def flash(level: str, msg: str):
    st.session_state.flash.append((level, msg))


def render_flash():
    for level, msg in st.session_state.pop("flash", []):
        getattr(st, level)(msg)
    st.session_state.flash = []


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso).astimezone()
    days = (datetime.now(timezone.utc).astimezone().date() - dt.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return dt.strftime("%d %b %Y")


def download_button(uid: int, doc: dm.Document, key: str, label="⬇️ Download Document", quiet=False):
    try:
        _, data = dm.read_document(uid, doc.id)
    except dm.DocumentError as exc:
        (st.caption if quiet else st.error)(f"⚠️ {exc}")
        return
    st.download_button(label, data=data, file_name=doc.filename,
                       mime=dm.mime_for(doc.file_type), key=key)


# --------------------------------------------------------------------- auth
def auth_page():
    st.title("🗂️ AI Document Vault")
    st.caption("Store your documents securely and ask the AI to find them.")
    login_tab, signup_tab = st.tabs(["Login", "Sign Up"])

    with login_tab:
        with st.form("login_form"):
            ident = st.text_input("Username or email")
            pw = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", use_container_width=True)
        if submitted:
            try:
                st.session_state.user = auth.login(ident, pw)
                st.rerun()
            except auth.AuthError as exc:
                st.error(str(exc))
            except DatabaseError:
                st.error("Database problem. Please try again in a moment.")

    with signup_tab:
        with st.form("signup_form"):
            username = st.text_input("Username")
            email = st.text_input("Email")
            pw1 = st.text_input("Password (min 8 characters)", type="password")
            pw2 = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create account", use_container_width=True)
        if submitted:
            try:
                st.session_state.user = auth.signup(username, email, pw1, pw2)
                st.rerun()
            except auth.AuthError as exc:
                st.error(str(exc))
            except DatabaseError:
                st.error("Database problem. Please try again in a moment.")


# ------------------------------------------------------------------ sidebar
def sidebar(user: dict):
    uid = user["id"]
    with st.sidebar:
        st.header(f"👤 {user['username']}")
        st.caption(user["email"])
        st.metric("Documents", dm.count_documents(uid))
        st.divider()

        st.subheader("Upload document")
        types = [e.lstrip(".") for e in dm.ALLOWED_TYPES]
        files = st.file_uploader(
            f"PDF, DOCX, TXT, JPG, PNG (max {dm.max_file_size() // (1024 * 1024)} MB)",
            type=types, accept_multiple_files=True,
            key=f"uploader_{st.session_state.uploader_key}",
        )
        if st.button("Upload", disabled=not files, use_container_width=True):
            ok = 0
            for f in files:
                try:
                    doc = dm.upload_document(uid, f.name, f.getvalue())
                    ok += 1
                    if doc.filename != f.name:
                        flash("info", f"'{f.name}' was saved as '{doc.filename}'.")
                except dm.DocumentError as exc:
                    flash("error", f"{f.name}: {exc}")
                except DatabaseError:
                    flash("error", f"{f.name}: database error, please retry.")
            if ok:
                flash("success", f"Uploaded {ok} document(s).")
            st.session_state.uploader_key += 1
            st.rerun()

        st.divider()
        if st.button("Logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()


# --------------------------------------------------------------------- chat
def choose_doc(doc_id: int):
    st.session_state.messages.append(
        {"role": "assistant", "content": "Here's the document you chose.", "status": "found", "doc_ids": [doc_id]}
    )


def render_message(uid: int, i: int, msg: dict):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] != "assistant":
            return
        for doc_id in msg.get("doc_ids", []):
            doc = dm.get_document(uid, doc_id)
            if doc is None:
                st.caption("⚠️ This document has since been deleted.")
            elif msg["status"] == "found":
                st.markdown(f"### 📄 {doc.filename}")
                st.caption(f"{doc.file_type} · {fmt_size(doc.file_size)} · Uploaded {fmt_date(doc.upload_date)}")
                download_button(uid, doc, key=f"chat_dl_{i}_{doc.id}")
            else:
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"📄 **{doc.filename}**  \n{doc.file_type} · {fmt_size(doc.file_size)} · {fmt_date(doc.upload_date)}")
                c2.button("Choose", key=f"pick_{i}_{doc.id}", on_click=choose_doc, args=(doc.id,))


def chat_section(uid: int):
    st.subheader("🤖 Document assistant")
    if not agent.llm_configured():
        st.caption("Running in filename-matching mode. Add OPENAI_API_KEY to .env for smarter matching.")
    box = st.container(height=420, border=True)
    with box:
        if not st.session_state.messages:
            with st.chat_message("assistant"):
                st.markdown("Hi! Ask me for a document, e.g. *“Give me my passport”* or *“Do I have electricity_bill?”*")
        for i, msg in enumerate(st.session_state.messages):
            render_message(uid, i, msg)

    prompt = st.chat_input("Ask for a document…")
    if prompt:
        res = agent.handle_request(uid, prompt)
        msgs = st.session_state.messages
        msgs.append({"role": "user", "content": prompt})
        msgs.append({"role": "assistant", "content": res.message, "status": res.status,
                     "doc_ids": [d.id for d in res.documents]})
        st.session_state.messages = msgs[-50:]
        st.rerun()


# ---------------------------------------------------------------- documents
def documents_section(uid: int):
    st.subheader("🕑 Recent uploads")
    recent = dm.list_documents(uid)[:5]
    if recent:
        st.dataframe(
            [{"Document": d.filename, "Type": d.file_type, "Size": fmt_size(d.file_size),
              "Uploaded": fmt_date(d.upload_date)} for d in recent],
            hide_index=True, use_container_width=True,
        )
    else:
        st.info("No documents yet. Upload one from the sidebar.")

    st.subheader("📁 All documents")
    pending = st.session_state.confirm_delete
    if pending is not None:
        doc = dm.get_document(uid, pending)
        if doc:
            st.warning(f"Delete **{doc.filename}** permanently?")
            a, b, _ = st.columns([1, 1, 6])
            if a.button("Yes, delete", type="primary"):
                dm.delete_document(uid, pending)
                st.session_state.confirm_delete = None
                flash("success", f"Deleted {doc.filename}.")
                st.rerun()
            if b.button("Cancel"):
                st.session_state.confirm_delete = None
                st.rerun()
        else:
            st.session_state.confirm_delete = None

    q = st.text_input("Search documents", placeholder="Filter by file name…")
    docs = dm.list_documents(uid, query=q)
    if not docs:
        st.caption("No matching documents.")
        return

    widths = [4, 1, 1, 2, 2, 1]
    for col, title in zip(st.columns(widths), ["Document", "Type", "Size", "Uploaded", "Action", ""]):
        col.markdown(f"**{title}**")
    for d in docs[:LIST_LIMIT]:
        c = st.columns(widths)
        c[0].write(f"📄 {d.filename}")
        c[1].write(d.file_type)
        c[2].write(fmt_size(d.file_size))
        c[3].write(fmt_date(d.upload_date))
        with c[4]:
            download_button(uid, d, key=f"list_dl_{d.id}", label="Download", quiet=True)
        if c[5].button("🗑️", key=f"del_{d.id}", help="Delete"):
            st.session_state.confirm_delete = d.id
            st.rerun()
    if len(docs) > LIST_LIMIT:
        st.caption(f"Showing {LIST_LIMIT} of {len(docs)}. Use search to narrow down.")


# --------------------------------------------------------------------- main
def main_page(user: dict):
    uid = user["id"]
    sidebar(user)
    st.title(f"👋 Welcome, {user['username']}")
    render_flash()
    chat_section(uid)
    documents_section(uid)


def main():
    try:
        init_db()
    except DatabaseError:
        st.error("Could not initialise the database. Check file permissions for the data/ folder.")
        st.stop()
    init_state()
    user = st.session_state.user
    if user is not None and not auth.user_exists(user["id"]):
        st.session_state.clear()
        st.rerun()
    if user is None:
        auth_page()
        return
    try:
        main_page(user)
    except DatabaseError:
        st.error("We're having trouble reaching the database. Please try again.")


main()
