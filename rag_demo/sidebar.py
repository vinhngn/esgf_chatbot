from constants import SCHEMA_IMG_PATH, LANGCHAIN_IMG_PATH
import streamlit as st
import streamlit.components.v1 as components
import os


def ChangeButtonColour(wgt_txt, wch_hex_colour="12px"):
    htmlstr = f"""
    <script>
        var elements = window.parent.document.querySelectorAll('*'), i;
        for (i = 0; i < elements.length; ++i) {{
            if (elements[i].innerText == '{wgt_txt}') {{
                elements[i].style.color = '{wch_hex_colour}';
            }}
        }}
    </script>
    """
    components.html(htmlstr, height=0, width=0)


def sidebar():
    with st.sidebar:
        # Base path for this script
        base_path = os.path.dirname(os.path.abspath(__file__))

        # Absolute path to local image
        gcmd_img_path = os.path.join(base_path, "images", "GCMD+.png")

        # Images
        st.markdown(
            "This is the GCMD+ taxonomy schema used to organize climate science concepts in our knowledge graph:"
        )
        st.image(gcmd_img_path, width=400)

        st.markdown(
            f"""This is how the Chatbot flow goes:<br>
            <img style="width: 70%; height: auto;" src="{LANGCHAIN_IMG_PATH}"/>""",
            unsafe_allow_html=True,
        )

        # Section Title
        st.markdown("**Questions you can ask:**")

        # Optional: robust CSS (not required if going vertical)
        st.markdown(
            """
            <style>
                button[kind="secondary"] {
                    white-space: normal !important;
                    word-wrap: break-word !important;
                }
            </style>
        """,
            unsafe_allow_html=True,
        )

        # Sample questions displayed vertically
        sample_questions = [
            "Show regional climate models that predict precipitation over Florida, USA",
            "Show the components, shared models, and realm for ACCESS models",
            "Show all models produced by NASA-GISS, their components, and any other models that use the same components",
            "Show all experiments using AGCM models",
            "What is the frequency, resolution, and realm associated with the model 'NorESM2-LM'?",
            "Which driving models are linked to regional climate models that predict variable pr?",
            "Show me all variables related to the model 'HadGEM3-GC31-LL'",
            "Which variables are associated with the experiment historical, and which models (sources) provide them?"
        ]

        for question in sample_questions:
            if st.button(question, key=question):
                st.session_state["sample"] = question
