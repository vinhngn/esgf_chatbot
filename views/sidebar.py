"""
Sidebar view - sample questions per database + schema images.
No business logic here.
"""

from __future__ import annotations

import os

import streamlit as st

from constants import LANGCHAIN_IMG_PATH


def sidebar(database: str) -> None:
    with st.sidebar:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gcmd_img_path = os.path.join(base_path, "images", "GCMD+.png")

        st.markdown(
            "This is the GCMD+ taxonomy schema used to organize climate science "
            "concepts in our knowledge graph:"
        )
        if os.path.exists(gcmd_img_path):
            st.image(gcmd_img_path, width=400)

        st.markdown(
            f"""This is how the Chatbot flow goes:<br>
            <img style="width: 70%; height: auto;" src="{LANGCHAIN_IMG_PATH}"/>""",
            unsafe_allow_html=True,
        )

        st.markdown("**Questions you can ask:**")

        st.markdown(
            """<style>
                button[kind="secondary"] {
                    white-space: normal !important;
                    word-wrap: break-word !important;
                }
            </style>""",
            unsafe_allow_html=True,
        )

        if database == "twitter":
            sample_questions = [
                "Who are the top 5 users that Neo4j follows?",
                "List the 5 most recent tweets by neo4j",
                "Which users follow Neo4j and have more than 1000 followers?",
                "What are the top 3 hashtags used in tweets by neo4j?",
                "Who does neo4j interact with most frequently?",
                "List the top 5 tweets with the most favorites",
                "Find users who have retweeted tweets mentioning Neo4j",
                "What is the average number of followers for users who follow neo4j?",
            ]

        elif database == "movies":
            sample_questions = [
                "Show all movies released after 2000",
                "Who directed The Matrix?",
                "List all actors who appeared in Cloud Atlas",
                "Which movies did Tom Hanks act in?",
                "Show the top 5 movies with the most votes",
                "Who wrote the screenplay for V for Vendetta?",
                "List all movies that Keanu Reeves acted in",
                "Which directors have made more than 3 movies in the database?",
            ]

        elif database == "recommendations":
            sample_questions = [
                "Show the top 10 highest rated movies",
                "Which movies are in the Action genre?",
                "List all movies directed by Christopher Nolan",
                "What movies has user 1 rated?",
                "Show movies with a rating above 8.5",
                "Which actors appeared in Inception?",
                "List the top 5 movies by revenue",
                "What genres does the movie Interstellar belong to?",
            ]

        elif database == "northwind":
            sample_questions = [
                "List all products in the Beverages category",
                "Which suppliers are from Germany?",
                "Show the top 5 most expensive products",
                "Which customers have placed more than 10 orders?",
                "List all products that are discontinued",
                "Which supplier provides the most products?",
                "Show all orders shipped to France",
                "What is the total number of products per category?",
            ]

        else:
            # Default: climate database
            sample_questions = [
                "Show regional climate models that predict precipitation over Florida, USA",
                "Show the components, shared models, and realm for ACCESS models",
                "Show all models produced by NASA-GISS, their components, and any other models that use the same components",
                "Show all experiments using AGCM models",
                "What is the frequency, resolution, and realm associated with the model 'NorESM2-LM'?",
                "Which driving models are linked to regional climate models that predict variable pr?",
                "Show me all variables related to the model 'HadGEM3-GC31-LL'",
                "Which variables are associated with the experiment historical, and which models (sources) provide them?",
            ]

        for question in sample_questions:
            if st.button(question, key=question):
                st.session_state["sample"] = question
