"""
Webhook Server for Text2Cypher Evaluation
Uses OpenAI to generate Cypher queries from natural language questions
Imports templates from rag_demo/templates/cypher_climate_template.py
"""
import re
import sys
import os

# Add rag_demo to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'rag_demo'))

from flask import Flask, request, jsonify
from openai import OpenAI
from templates.cypher_climate_template import (
    CYPHER_GENERATION_MOVIES_TEMPLATE,
    CYPHER_GENERATION_CLIMATE_TEMPLATE,
    CYPHER_GENERATION_RECOMMENDATIONS_TEMPLATE,
    CYPHER_GENERATION_NORTHWIND_TEMPLATE,
    CYPHER_GENERATION_TWITTER_TEMPLATE,
)

app = Flask(__name__)

# Configuration
import os
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = "gpt-4o-mini"

# Database to template mapping
TEMPLATES = {
    "movies": CYPHER_GENERATION_MOVIES_TEMPLATE,
    "climate": CYPHER_GENERATION_CLIMATE_TEMPLATE,
    "recommendations": CYPHER_GENERATION_RECOMMENDATIONS_TEMPLATE,
    "northwind": CYPHER_GENERATION_NORTHWIND_TEMPLATE,
    "twitter": CYPHER_GENERATION_TWITTER_TEMPLATE,
}

# Current database (can be changed via endpoint or config)
CURRENT_DB = "recommendations"

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)


def generate_cypher(question: str, schema: str, database: str = None) -> str:
    """Generate Cypher query using OpenAI with template from cypher_climate_template.py"""
    
    db = database or CURRENT_DB
    template = TEMPLATES.get(db, CYPHER_GENERATION_MOVIES_TEMPLATE)
    
    # Use replace instead of format to avoid issues with Cypher {property} syntax
    prompt = template.replace("{schema}", schema).replace("{question}", f"Question: {question}\n\nCypher Query:")

    try:
        print(f"[Webhook] Calling OpenAI API with {db} template...")
        
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1024
        )
        
        cypher = response.choices[0].message.content.strip()
        
        # Clean up the response
        cypher = re.sub(r'```cypher\s*', '', cypher, flags=re.IGNORECASE)
        cypher = re.sub(r'```\s*', '', cypher)
        cypher = cypher.rstrip(';').strip()
        
        print(f"[Webhook] OpenAI response received")
        return cypher
        
    except Exception as e:
        print(f"[Webhook] OpenAI error: {e}")
        return ""


@app.route('/api/text2cypher', methods=['POST'])
def text2cypher():
    """Main endpoint for generating Cypher queries"""
    
    try:
        data = request.get_json()
        question = data.get('question', '')
        schema = data.get('schema', '')
        database = data.get('database', CURRENT_DB)  # Allow specifying database
        
        if not question:
            return jsonify({
                'cypher_query': '',
                'result': [],
                'error': 'No question provided'
            })
        
        print(f"\n[Webhook] Question: {question[:80]}...")
        print(f"[Webhook] Database: {database}")
        print(f"[Webhook] Schema length: {len(schema)} chars")
        
        # Generate Cypher using template from cypher_climate_template.py
        cypher_query = generate_cypher(question, schema, database)
        
        print(f"[Webhook] Generated: {cypher_query[:100] if cypher_query else 'EMPTY'}...")
        
        return jsonify({
            'cypher_query': cypher_query,
            'result': [],
            'error': None
        })
        
    except Exception as e:
        print(f"[Webhook] Error: {str(e)}")
        return jsonify({
            'cypher_query': '',
            'result': [],
            'error': str(e)
        })


@app.route('/api/set_database', methods=['POST'])
def set_database():
    """Change the current database template"""
    global CURRENT_DB
    data = request.get_json()
    db = data.get('database', '').lower()
    
    if db in TEMPLATES:
        CURRENT_DB = db
        return jsonify({'status': 'ok', 'database': CURRENT_DB})
    else:
        return jsonify({
            'status': 'error', 
            'message': f'Unknown database: {db}',
            'available': list(TEMPLATES.keys())
        })


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok', 
        'model': MODEL,
        'database': CURRENT_DB,
        'available_databases': list(TEMPLATES.keys())
    })


if __name__ == '__main__':
    print("=" * 60)
    print("Text2Cypher Webhook Server")
    print(f"Model: {MODEL}")
    print(f"Database: {CURRENT_DB}")
    print(f"Available: {list(TEMPLATES.keys())}")
    print("=" * 60)
    print("\nTemplates imported from:")
    print("  rag_demo/templates/cypher_climate_template.py")
    print("=" * 60)
    print("\nStarting server on http://127.0.0.1:8000")
    print("Endpoints:")
    print("  POST /api/text2cypher - Generate Cypher")
    print("  POST /api/set_database - Change database")
    print("  GET  /health - Health check")
    print("=" * 60)
    
    app.run(host='127.0.0.1', port=8000, debug=False)
