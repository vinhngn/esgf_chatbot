from constants import SCHEMA_IMG_PATH, LANGCHAIN_IMG_PATH
import streamlit as st
import streamlit.components.v1 as components

def ChangeButtonColour(wgt_txt, wch_hex_colour='12px'):
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
        # Images
        st.markdown("This is the GCMD+ taxonomy schema used to organize climate science concepts in our knowledge graph:")
        st.image("rag_demo/images/GCMD+.png", width=400)

        st.markdown(
            f"""This is how the Chatbot flow goes:<br>
            <img style="width: 70%; height: auto;" src="{LANGCHAIN_IMG_PATH}"/>""",
            unsafe_allow_html=True
        )

        # Section Title
        st.markdown("**Questions you can ask:**")

        # Optional: robust CSS (not required if going vertical)
        st.markdown("""
            <style>
                button[kind="secondary"] {
                    white-space: normal !important;
                    word-wrap: break-word !important;
                }
            </style>
        """, unsafe_allow_html=True)

        # Sample questions displayed vertically
        sample_questions = [
            "Which papers mention anomalous temperature regimes such as cold air outbreaks (CAOs) or warm waves (WWs) in relation to North America, specifically in the sentences where these terms appear?",
            "Which papers discuss ocean circulation processes—such as thermohaline circulation—in oceanic regions that include either “North” or “South” in their names?",
            "What ocean circulation processes are associated with the Southern Ocean and mentioned in connection with upwelling?",
            "Which papers mention the Pacific-North American (PNA) pattern in connection with locations in the United States?",
            "Which papers mention CMIP5 models and the North Atlantic Oscillation (NAO) in the context of the Southeast United States?"


        ]

        for question in sample_questions:
            if st.button(question, key=question):
                st.session_state["sample"] = question
