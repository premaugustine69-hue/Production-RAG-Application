"""This file should be imported if and only if you want to run the UI locally."""
import nest_asyncio
nest_asyncio.apply()
import base64
import logging
import subprocess
import time
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any

import gradio as gr  # type: ignore
from fastapi import FastAPI
from gradio.themes.utils.colors import slate  # type: ignore
from injector import inject, singleton
from llama_index.core.llms import ChatMessage, ChatResponse, MessageRole
from llama_index.core.types import TokenGen
from pydantic import BaseModel

from production_rag.constants import PROJECT_ROOT_PATH
from production_rag.di import global_injector
from production_rag.open_ai.extensions.context_filter import ContextFilter
from production_rag.server.chat.chat_service import ChatService, CompletionGen
from production_rag.server.chunks.chunks_service import Chunk, ChunksService
from production_rag.server.ingest.ingest_service import IngestService
from production_rag.server.recipes.summarize.summarize_service import SummarizeService
from production_rag.settings.settings import settings
from production_rag.ui.images import logo_svg

logger = logging.getLogger(__name__)

THIS_DIRECTORY_RELATIVE = Path(__file__).parent.relative_to(PROJECT_ROOT_PATH)
# Should be "production_rag/ui/avatar-bot.ico"
AVATAR_BOT = THIS_DIRECTORY_RELATIVE / "avatar-bot.ico"

UI_TAB_TITLE = "Production Grade RAG Application"

SOURCES_SEPARATOR = "<hr>Sources: \n"


class Modes(str, Enum):
    RAG_MODE = "RAG"
    SEARCH_MODE = "Search"
    BASIC_CHAT_MODE = "Basic"
    SUMMARIZE_MODE = "Summarize"


MODES: list[Modes] = [
    Modes.RAG_MODE,
    Modes.SEARCH_MODE,
    Modes.BASIC_CHAT_MODE,
    Modes.SUMMARIZE_MODE,
]


class Source(BaseModel):
    file: str
    page: str
    text: str

    class Config:
        frozen = True

    @staticmethod
    def curate_sources(sources: list[Chunk]) -> list["Source"]:
        curated_sources: list[Source] = []
        seen: set[str] = set()
        for chunk in sources:
            meta = chunk.document.doc_metadata or {}
            file_name = meta.get("file_name", "-")
            page_label = meta.get("page_label", "-")
            key = f"{file_name}-{page_label}"
            if key in seen:
                continue
            seen.add(key)
            curated_sources.append(Source(file=file_name, page=page_label, text=chunk.text))
        return curated_sources


@singleton
class ProductionGradeRAGApplicationUI:
    @inject
    def __init__(
        self,
        ingest_service: IngestService,
        chat_service: ChatService,
        chunks_service: ChunksService,
        summarizeService: SummarizeService,
    ) -> None:
        self._ingest_service = ingest_service
        self._chat_service = chat_service
        self._chunks_service = chunks_service
        self._summarize_service = summarizeService

        # Cache the UI blocks
        self._ui_block: gr.Blocks | None = None

        self._selected_filename: str | None = None

        # Initialize system prompt based on default mode
        default_mode_map = {mode.value: mode for mode in Modes}
        self._default_mode = default_mode_map.get(
            settings().ui.default_mode, Modes.RAG_MODE
        )
        self._system_prompt = self._get_default_system_prompt(self._default_mode)

    # ---------------------------- Ollama helpers ----------------------------

    def _available_ollama_models(self) -> list[str]:
        """Return installed Ollama models (fallback to a few safe defaults)."""
        try:
            out = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, check=True
            ).stdout.splitlines()
            # skip header, first column is the tag (e.g. llama3.1:latest)
            models = [line.split()[0] for line in out[1:] if line.strip()]
            return models or ["gemma3:1b", "llama3.1:latest"]
        except Exception as e:
            logger.warning(f"Could not fetch Ollama models: {e}")
            return ["gemma3:1b", "llama3.1:latest"]

    def _default_model_value(self, choices: list[str]) -> str:
        cfg = (settings().ollama.llm_model or "").strip()
        if cfg in choices:
            return cfg
        if cfg and f"{cfg}:latest" in choices:
            return f"{cfg}:latest"
        return choices[0] if choices else "gemma3:1b"

    def _set_model(self, model_name: str) -> str:
        """Switch the active Ollama model in settings()."""
        cfg = settings()
        if cfg.llm.mode != "ollama":
            return f"Error: LLM mode is '{cfg.llm.mode}', not 'ollama'."
        cfg.ollama.llm_model = model_name
        return f"Model switched to: {model_name}"

    # --------------------------- File list helpers --------------------------

    def _list_ingested_files(self) -> list[list[str]]:
        files = set()
        for ing in self._ingest_service.list_ingested():
            meta = ing.doc_metadata or {}
            files.add(meta.get("file_name", "[FILE NAME MISSING]"))
        return [[name] for name in files]

    def _upload_file(self, files: list[str]) -> list[list[str]]:
        """Ingest uploaded files and return refreshed list for the gr.List component."""
        if not files:
            return self._list_ingested_files()

        paths = [Path(p) for p in files]

        # Replace docs with same filename
        target_names = {p.name for p in paths}
        to_delete: list[str] = []
        for doc in self._ingest_service.list_ingested():
            meta = doc.doc_metadata or {}
            if meta.get("file_name") in target_names:
                to_delete.append(doc.doc_id)
        for doc_id in to_delete:
            self._ingest_service.delete(doc_id)

        self._ingest_service.bulk_ingest([(str(p.name), p) for p in paths])
        return self._list_ingested_files()

    def _delete_all_files(self) -> list[Any]:
        for ing in self._ingest_service.list_ingested():
            self._ingest_service.delete(ing.doc_id)
        self._selected_filename = None
        return [
            self._list_ingested_files(),
            gr.update(interactive=False),  # delete selected file button
            gr.update(interactive=False),  # de-select button
            gr.update(value="All files"),  # selected text
        ]

    def _delete_selected_file(self) -> list[Any]:
        if not self._selected_filename:
            return [
                self._list_ingested_files(),
                gr.update(interactive=False),
                gr.update(interactive=False),
                gr.update(value="All files"),
            ]
        for ing in self._ingest_service.list_ingested():
            meta = ing.doc_metadata or {}
            if meta.get("file_name") == self._selected_filename:
                self._ingest_service.delete(ing.doc_id)
        self._selected_filename = None
        return [
            self._list_ingested_files(),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(value="All files"),
        ]

    def _deselect_selected_file(self) -> list[Any]:
        self._selected_filename = None
        return [
            gr.update(interactive=False),  # delete selected button
            gr.update(interactive=False),  # de-select button
            gr.update(value="All files"),
        ]

    def _selected_a_file(self, select_data: gr.SelectData) -> list[Any]:
        # For a one-column list, value is the file name string
        self._selected_filename = str(select_data.value)
        return [
            gr.update(interactive=True),   # enable delete selected
            gr.update(interactive=True),   # enable de-select
            gr.update(value=self._selected_filename),
        ]

    # ------------------------------- Chat -----------------------------------

    def _chat(
        self, message: str, history: list[list[str]], mode: Modes, *_: Any
    ) -> Any:
        def yield_deltas(completion_gen: CompletionGen) -> Iterable[str]:
            full_response: str = ""
            stream = completion_gen.response
            for delta in stream:
                if isinstance(delta, str):
                    full_response += str(delta)
                elif isinstance(delta, ChatResponse):
                    full_response += delta.delta or ""
                yield full_response
                time.sleep(0.02)

            # Attach sources (RAG)
            if completion_gen.sources:
                full_response += SOURCES_SEPARATOR
                cur_sources = Source.curate_sources(completion_gen.sources)
                sources_text = "\n\n\n"
                used_files = set()
                for index, source in enumerate(cur_sources, start=1):
                    if f"{source.file}-{source.page}" not in used_files:
                        sources_text += f"{index}. {source.file} (page {source.page}) \n\n"
                        used_files.add(f"{source.file}-{source.page}")
                sources_text += "<hr>\n\n"
                full_response += sources_text
            yield full_response

        # Build history (trim to 20)
        history_msgs: list[ChatMessage] = []
        for turn in history[:20]:
            history_msgs.append(ChatMessage(content=turn[0], role=MessageRole.USER))
            if len(turn) > 1 and turn[1] is not None:
                history_msgs.append(
                    ChatMessage(
                        content=turn[1].split(SOURCES_SEPARATOR)[0],
                        role=MessageRole.ASSISTANT,
                    )
                )

        new_message = ChatMessage(content=message, role=MessageRole.USER)
        all_messages = [*history_msgs, new_message]

        # Add system prompt if configured
        if self._system_prompt:
            all_messages.insert(
                0, ChatMessage(content=self._system_prompt, role=MessageRole.SYSTEM)
            )

        match mode:
            case Modes.RAG_MODE:
                context_filter = None
                if self._selected_filename:
                    doc_ids = [
                        ing.doc_id
                        for ing in self._ingest_service.list_ingested()
                        if (ing.doc_metadata or {}).get("file_name") == self._selected_filename
                    ]
                    context_filter = ContextFilter(docs_ids=doc_ids)
                stream = self._chat_service.stream_chat(
                    messages=all_messages,
                    use_context=True,
                    context_filter=context_filter,
                )
                yield from yield_deltas(stream)

            case Modes.BASIC_CHAT_MODE:
                stream = self._chat_service.stream_chat(
                    messages=all_messages,
                    use_context=False,
                )
                yield from yield_deltas(stream)

            case Modes.SEARCH_MODE:
                response = self._chunks_service.retrieve_relevant(
                    text=message, limit=4, prev_next_chunks=0
                )
                sources = Source.curate_sources(response)
                yield "\n\n\n".join(
                    f"{idx}. **{s.file} (page {s.page})**\n {s.text}"
                    for idx, s in enumerate(sources, start=1)
                )

            case Modes.SUMMARIZE_MODE:
                context_filter = None
                if self._selected_filename:
                    doc_ids = [
                        ing.doc_id
                        for ing in self._ingest_service.list_ingested()
                        if (ing.doc_metadata or {}).get("file_name") == self._selected_filename
                    ]
                    context_filter = ContextFilter(docs_ids=doc_ids)
                token_gen: TokenGen = self._summarize_service.stream_summarize(
                    use_context=True,
                    context_filter=context_filter,
                    instructions=message,
                )
                out = ""
                for tok in token_gen:
                    out += str(tok)
                    yield out

    # ------------------------ Mode/system-prompt helpers --------------------

    @staticmethod
    def _get_default_system_prompt(mode: Modes) -> str:
        if mode == Modes.RAG_MODE:
            return settings().ui.default_query_system_prompt
        if mode == Modes.BASIC_CHAT_MODE:
            return settings().ui.default_chat_system_prompt
        if mode == Modes.SUMMARIZE_MODE:
            return settings().ui.default_summarization_system_prompt
        return ""

    @staticmethod
    def _get_default_mode_explanation(mode: Modes) -> str:
        if mode == Modes.RAG_MODE:
            return "Get contextualized answers from selected files."
        if mode == Modes.SEARCH_MODE:
            return "Find relevant chunks of text in selected files."
        if mode == Modes.BASIC_CHAT_MODE:
            return "Chat with the LLM using its training data. Files are ignored."
        if mode == Modes.SUMMARIZE_MODE:
            return "Generate a summary of the selected files. Prompt to customize the result."
        return ""

    def _set_system_prompt(self, system_prompt_input: str) -> None:
        logger.info(f"Setting system prompt to: {system_prompt_input}")
        self._system_prompt = system_prompt_input

    def _set_current_mode(self, mode: Modes) -> list[Any]:
        self.mode = mode
        self._system_prompt = self._get_default_system_prompt(mode)
        explanation = self._get_default_mode_explanation(mode)
        # Gradio-compatible updates (no gr.Update typing)
        return [
            gr.update(placeholder=self._system_prompt, interactive=True),
            gr.update(value=explanation),
        ]
    def _list_ingested_files(self) -> list[list[str]]:
        files = set()
        for ing in self._ingest_service.list_ingested():
            meta = ing.doc_metadata or {}
            files.add(meta.get("file_name", "[FILE NAME MISSING]"))
        return [[name] for name in files]


    # --------------------------------- UI -----------------------------------

    def _build_ui_blocks(self) -> gr.Blocks:
        logger.debug("Creating the UI blocks")
        with gr.Blocks(
            title=UI_TAB_TITLE,
            theme=gr.themes.Soft(primary_hue=slate),
            css=(
                ".logo {display:flex;background-color:#C7BAFF;height:80px;border-radius:8px;"
                "align-content:center;justify-content:center;align-items:center;}"
                ".logo img {height:25%}"
                "#chatbot {flex-grow:1 !important;overflow:auto !important;min-height:480px;}"
            ),
        ) as blocks:
            # Header
            with gr.Row():
                gr.HTML(f"<div class='logo'/><img src={logo_svg} alt=Production Grade RAG Application></div")

            with gr.Row(equal_height=False):
                # ---------------- LEFT COLUMN (unchanged layout) ----------------
                with gr.Column(scale=3):
                    default_mode = self._default_mode
                    mode = gr.Radio(
                        [m.value for m in MODES],
                        label="Mode",
                        value=default_mode,
                    )

                    explanation_mode = gr.Textbox(
                        placeholder=self._get_default_mode_explanation(default_mode),
                        show_label=False,
                        max_lines=3,
                        interactive=False,
                    )

                    upload_button = gr.UploadButton(
                        "Upload File(s)", type="filepath", file_count="multiple", size="sm"
                    )

                    ingested_dataset = gr.List(
                        self._list_ingested_files,
                        headers=["File name"],
                        label="Ingested Files",
                        height=235,
                        interactive=False,
                        render=False,
                    )
                    upload_button.upload(
                        self._upload_file, inputs=upload_button, outputs=ingested_dataset
                    )
                    ingested_dataset.change(
                        self._list_ingested_files,
                        outputs=ingested_dataset,
                    )
                    ingested_dataset.render()

                    deselect_file_button = gr.Button(
                        "De-select selected file", size="sm", interactive=False
                    )
                    selected_text = gr.Textbox(
                        "All files", label="Selected for Query or Deletion", max_lines=1
                    )
                    delete_file_button = gr.Button(
                        "🗑️ Delete selected file",
                        size="sm",
                        visible=settings().ui.delete_file_button_enabled,
                        interactive=False,
                    )
                    delete_files_button = gr.Button(
                        "⚠️ Delete ALL files",
                        size="sm",
                        visible=settings().ui.delete_all_files_button_enabled,
                    )

                    deselect_file_button.click(
                        self._deselect_selected_file,
                        outputs=[delete_file_button, deselect_file_button, selected_text],
                    )
                    ingested_dataset.select(
                        fn=self._selected_a_file,
                        outputs=[delete_file_button, deselect_file_button, selected_text],
                    )
                    delete_file_button.click(
                        self._delete_selected_file,
                        outputs=[
                            ingested_dataset,
                            delete_file_button,
                            deselect_file_button,
                            selected_text,
                        ],
                    )
                    delete_files_button.click(
                        self._delete_all_files,
                        outputs=[
                            ingested_dataset,
                            delete_file_button,
                            deselect_file_button,
                            selected_text,
                        ],
                    )

                    system_prompt_input = gr.Textbox(
                        placeholder=self._system_prompt,
                        label="System Prompt",
                        lines=2,
                        interactive=True,
                        render=False,
                    )
                    mode.change(
                        self._set_current_mode,
                        inputs=mode,
                        outputs=[system_prompt_input, explanation_mode],
                    )
                    system_prompt_input.blur(
                        self._set_system_prompt,
                        inputs=system_prompt_input,
                    )

                # ---------------- RIGHT COLUMN (dropdown + status ABOVE chat) ---
                with gr.Column(scale=7, elem_id="col"):
                    # Model dropdown + status
                    choices = self._available_ollama_models()
                    model_dropdown = gr.Dropdown(
                        label="Select Ollama Model",
                        choices=choices,
                        value=self._default_model_value(choices),
                    )
                    status_box = gr.Textbox(label="Status", value="Ready", interactive=False)
                    model_dropdown.change(
                        fn=self._set_model,
                        inputs=model_dropdown,
                        outputs=status_box,
                    )

                    # Chat interface (no model name in label)
                    gr.ChatInterface(
                        fn=self._chat,
                        chatbot=gr.Chatbot(
                            label="",
                            elem_id="chatbot",
                            show_copy_button=True,
                            avatar_images=(None, AVATAR_BOT),
                        ),
                        additional_inputs=[mode, upload_button, system_prompt_input],
                    )

        return blocks

    def get_ui_blocks(self) -> gr.Blocks:
        if self._ui_block is None:
            self._ui_block = self._build_ui_blocks()
        return self._ui_block

    def mount_in_app(self, app: FastAPI, path: str) -> None:
        blocks = self.get_ui_blocks()
        blocks.queue()
        logger.info("Mounting the gradio UI, at path=%s", path)
        gr.mount_gradio_app(app, blocks, path=path, favicon_path=AVATAR_BOT)


if __name__ == "__main__":
    ui = global_injector.get(ProductionGradeRAGApplicationUI)
    _blocks = ui.get_ui_blocks()
    _blocks.queue()
    _blocks.launch(debug=False, show_api=False)

