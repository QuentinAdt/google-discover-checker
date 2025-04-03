from flask import Flask, request, jsonify, render_template_string
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import tempfile
import os
import logging
from urllib.parse import urljoin, urlparse
import json
from logging.handlers import RotatingFileHandler
import time

# Configuration du logging
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'app.log')

logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ajouter un handler pour écrire dans un fichier
file_handler = RotatingFileHandler(log_file, maxBytes=10485760, backupCount=5)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Ajouter un handler pour la console
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

app = Flask(__name__)

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

def check_robots_meta(soup):
    try:
        meta_robots_list = [
            soup.find('meta', attrs={'name': lambda x: x and x.lower() == 'robots'}),
            soup.find('meta', attrs={'name': 'ROBOTS'}),
            soup.find('meta', attrs={'name': 'Robots'}),
            soup.find('meta', content=lambda x: x and 'max-image-preview:large' in x.lower())
        ]
        
        for meta_robots in meta_robots_list:
            if meta_robots:
                content = meta_robots.get('content', '').lower()
                content_parts = [part.strip() for part in content.split(',')]
                normalized_content = ','.join(content_parts)
                normalized_content = normalized_content.replace(' : ', ':').replace(': ', ':').replace(' :', ':')
                if 'max-image-preview:large' in normalized_content:
                    return True
        
        html_content = str(soup)
        if 'max-image-preview:large' in html_content.lower():
            return True
            
        return False
    except Exception as e:
        logger.error(f"Error checking robots meta: {str(e)}")
        return False

def analyze_static_images(url):
    results = {}
    robots_meta_found = False
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }
        
        response = requests.get(url, headers=headers, timeout=30, verify=True)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        robots_meta_found = check_robots_meta(soup)
        
        for img in soup.find_all('img'):
            img_url = img.get('src', '')
            if not img_url:
                continue
                
            if not img_url.startswith(('http://', 'https://')):
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                else:
                    img_url = urljoin(url, img_url)
            
            try:
                img_response = requests.get(img_url, headers=headers, timeout=10, verify=True)
                img_response.raise_for_status()
                
                img_data = Image.open(BytesIO(img_response.content))
                results[img_url] = {
                    'width': img_data.width,
                    'height': img_data.height,
                    'static': True,
                    'dynamic': False,
                    'area': img_data.width * img_data.height
                }
            except Exception as e:
                logger.warning(f"Error processing image {img_url}: {str(e)}")
                continue
    except Exception as e:
        logger.error(f"Error in static analysis: {str(e)}")
    return results, robots_meta_found

def analyze_dynamic_images(url, max_retries=3):
    results = {}
    robots_meta_found = False
    retries = 0
    last_error = None
    
    while retries < max_retries:
        try:
            logger.info(f"Tentative {retries + 1}/{max_retries} d'analyse dynamique")
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    args=['--no-sandbox', '--disable-setuid-sandbox']
                )
                context = browser.new_context(
                    viewport={'width': 4000, 'height': 4000},
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
                )
                page = context.new_page()
                try:
                    page.goto(url, timeout=30000, wait_until='networkidle')
                    
                    robots_meta_found = page.evaluate('''
                        () => {
                            try {
                                const robotsMeta = document.querySelector('meta[name="robots"], meta[name="ROBOTS"], meta[name="Robots"]');
                                if (robotsMeta) {
                                    const content = robotsMeta.content.toLowerCase();
                                    return content.includes('max-image-preview:large');
                                }
                                
                                const allMetas = document.getElementsByTagName('meta');
                                for (const meta of allMetas) {
                                    const content = meta.content ? meta.content.toLowerCase() : '';
                                    if (content.includes('max-image-preview:large')) {
                                        return true;
                                    }
                                }
                                
                                const htmlContent = document.documentElement.innerHTML.toLowerCase();
                                return htmlContent.includes('max-image-preview:large');
                            } catch (e) {
                                console.error('Error checking robots meta:', e);
                                return false;
                            }
                        }
                    ''')
                    
                    images = page.evaluate('''
                        () => {
                            try {
                                const images = document.getElementsByTagName('img');
                                return Array.from(images).map(img => ({
                                    src: img.src,
                                    width: img.naturalWidth || 0,
                                    height: img.naturalHeight || 0
                                }));
                            } catch (e) {
                                console.error('Error getting images:', e);
                                return [];
                            }
                        }
                    ''')
                    
                    for img in images:
                        if img['src'] and img['width'] and img['height']:
                            results[img['src']] = {
                                'width': img['width'],
                                'height': img['height'],
                                'static': False,
                                'dynamic': True,
                                'area': img['width'] * img['height']
                            }
                    
                    # Si nous arrivons ici, l'analyse a réussi, sortir de la boucle
                    logger.info(f"Analyse dynamique réussie à la tentative {retries + 1}")
                    return results, robots_meta_found
                    
                except PlaywrightTimeout:
                    last_error = f"Timeout loading page: {url}"
                    logger.warning(last_error)
                except Exception as e:
                    last_error = str(e)
                    logger.error(f"Error in dynamic analysis: {last_error}")
                finally:
                    browser.close()
        except Exception as e:
            last_error = str(e)
            logger.error(f"Error initializing Playwright: {last_error}")
        
        retries += 1
        if retries < max_retries:
            logger.info(f"Nouvelle tentative ({retries + 1}/{max_retries}) dans 2 secondes...")
            time.sleep(2)
    
    logger.warning(f"Échec de l'analyse dynamique après {max_retries} tentatives. Dernière erreur: {last_error}")
    return results, robots_meta_found

def merge_results(static_results, dynamic_results):
    merged = {}
    
    all_urls = set(list(static_results.keys()) + list(dynamic_results.keys()))
    
    for url in all_urls:
        static_data = static_results.get(url, {})
        dynamic_data = dynamic_results.get(url, {})
        
        width = dynamic_data.get('width', static_data.get('width', 0))
        height = dynamic_data.get('height', static_data.get('height', 0))
        
        if width > 0 and height > 0:  # Ignorer les images invalides
            merged[url] = {
                'url': url,
                'width': width,
                'height': height,
                'static': url in static_results,
                'dynamic': url in dynamic_results,
                'area': width * height
            }
    
    # Trier par taille et prendre les 3 plus grandes images
    sorted_images = sorted(merged.values(), key=lambda x: x['area'], reverse=True)[:3]
    return sorted_images

def analyze_url(url):
    try:
        # Analyze images
        logger.info("Début de l'analyse des images statiques")
        static_results, static_robots = analyze_static_images(url)
        logger.info(f"Résultats statiques : {len(static_results)} images trouvées, robots meta trouvé : {static_robots}")
        
        logger.info("Début de l'analyse des images dynamiques")
        dynamic_results, dynamic_robots = analyze_dynamic_images(url)
        logger.info(f"Résultats dynamiques : {len(dynamic_results)} images trouvées, robots meta trouvé : {dynamic_robots}")
        
        # Merge and sort results
        largest_images = merge_results(static_results, dynamic_results)
        logger.info(f"Fusion des résultats : {len(largest_images)} images retenues")
        
        # Check for high resolution images
        has_high_res = any(img['width'] >= 1200 for img in largest_images)
        
        return {
            'url': url,
            'robots_meta': {
                'max_image_preview_large_found': static_robots or dynamic_robots,
                'found_in_static': static_robots,
                'found_in_dynamic': dynamic_robots
            },
            'discover_compatibility': {
                'has_large_images': has_high_res,
                'minimum_width_required': 1200,
                'compatible': has_high_res and (static_robots or dynamic_robots)
            },
            'largest_images': largest_images
        }, None
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse : {str(e)}")
        logger.exception("Stack trace complète :")
        return None, str(e)

@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    logger.info("Nouvelle requête d'analyse reçue via API")
    
    if not request.is_json:
        logger.error("La requête n'est pas au format JSON")
        return jsonify({'error': 'Content-Type must be application/json'}), 400
        
    data = request.get_json()
    url = data.get('url')
    
    logger.info(f"URL à analyser : {url}")
    
    if not url:
        logger.error("URL manquante dans la requête")
        return jsonify({'error': 'URL is required'}), 400
        
    if not is_valid_url(url):
        logger.error(f"Format d'URL invalide : {url}")
        return jsonify({'error': 'Invalid URL format'}), 400
    
    results, error = analyze_url(url)
    
    if error:
        return jsonify({
            'error': 'Internal server error', 
            'details': error,
            'type': 'Exception'
        }), 500
    
    logger.info("Analyse terminée avec succès")
    return jsonify(results)

# Template HTML pour la page d'accueil simplifiée
HOME_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Analyse d'Images</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 20px auto; padding: 0 20px; }
        .container { margin-top: 30px; }
        .form-group { margin-bottom: 20px; }
        input[type="text"] { width: 100%; padding: 8px; margin-top: 5px; }
        button { background: #4CAF50; color: white; padding: 10px 20px; border: none; cursor: pointer; }
        button:hover { background: #45a049; }
        button:disabled { background: #cccccc; cursor: not-allowed; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .success { color: #4CAF50; }
        .error { color: red; }
        .warning { color: #ff9800; }
        img { max-width: 200px; max-height: 200px; }
        .progress-container {
            width: 100%;
            background-color: #f1f1f1;
            border-radius: 5px;
            margin: 10px 0;
        }
        .progress-bar {
            height: 20px;
            background-color: #4CAF50;
            border-radius: 5px;
            width: 0%;
            transition: width 1s;
        }
        .progress-message {
            margin-top: 5px;
            font-style: italic;
        }
        .result-section {
            background-color: #f9f9f9;
            border-radius: 5px;
            padding: 15px;
            margin-bottom: 20px;
        }
        .card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }
        .card {
            border: 1px solid #ddd;
            border-radius: 5px;
            padding: 10px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .card img {
            width: 100%;
            height: auto;
            object-fit: cover;
            border-radius: 3px;
        }
        .card-content {
            padding: 10px 0;
        }
        .metadata {
            display: flex;
            justify-content: space-between;
            margin-bottom: 5px;
            font-size: 14px;
        }
        footer {
            margin-top: 30px;
            padding-top: 10px;
            border-top: 1px solid #ddd;
            text-align: center;
            font-size: 12px;
            color: #666;
        }
    </style>
    <script>
        function startAnalysis() {
            // Afficher l'indicateur de progression
            document.getElementById('progress-container').style.display = 'block';
            document.getElementById('analyze-button').disabled = true;
            document.getElementById('progress-message').innerText = 'Analyse des images statiques...';
            
            // Simuler les étapes de progression
            setTimeout(() => {
                document.getElementById('progress-bar').style.width = '20%';
            }, 500);
            
            setTimeout(() => {
                document.getElementById('progress-bar').style.width = '40%';
                document.getElementById('progress-message').innerText = 'Vérification des meta tags...';
            }, 2000);
            
            setTimeout(() => {
                document.getElementById('progress-bar').style.width = '60%';
                document.getElementById('progress-message').innerText = 'Analyse des images dynamiques...';
            }, 3500);
            
            setTimeout(() => {
                document.getElementById('progress-bar').style.width = '80%';
                document.getElementById('progress-message').innerText = 'Évaluation de la compatibilité Google Discover...';
            }, 5000);
            
            setTimeout(() => {
                document.getElementById('progress-bar').style.width = '100%';
                document.getElementById('progress-message').innerText = 'Analyse terminée, traitement des résultats...';
            }, 6000);
            
            // Soumettre le formulaire
            document.getElementById('analysis-form').submit();
        }
    </script>
</head>
<body>
    <div class="container">
        <h1>Analyse d'Images</h1>
        <form id="analysis-form" action="/analyze" method="post" onsubmit="startAnalysis(); return false;">
            <div class="form-group">
                <label for="url">URL à analyser :</label>
                <input type="text" id="url" name="url" placeholder="https://example.com" value="https://www.zonebourse.com/actualite-bourse/Quels-secteurs-privilegier-en-periode-de-crise-boursiere--49519033/" required>
                <button type="submit" id="analyze-button">Analyser</button>
            </div>
        </form>
        
        <div id="progress-container" class="progress-container" style="display: none;">
            <div id="progress-bar" class="progress-bar"></div>
            <div id="progress-message" class="progress-message">Préparation de l'analyse...</div>
        </div>
        
        {% if error %}
        <div class="error">
            <p>{{ error }}</p>
        </div>
        {% endif %}
        
        {% if results %}
        <div class="results">
            <h2>Résultats de l'analyse</h2>
            
            <div class="result-section">
                <h3>Meta Robots Tag</h3>
                {% if results.robots_meta.max_image_preview_large_found %}
                <p class="success">✓ Tag meta robots avec max-image-preview:large trouvé</p>
                {% else %}
                <p class="warning">⚠ Tag meta robots avec max-image-preview:large non trouvé</p>
                <p>Google recommande d'ajouter <code>&lt;meta name="robots" content="max-image-preview:large"&gt;</code> dans la section head de votre page.</p>
                {% endif %}
            </div>
            
            <div class="result-section">
                <h3>Compatibilité avec Google Discover</h3>
                {% if results.discover_compatibility.compatible %}
                <p class="success">✓ Compatible avec Google Discover</p>
                {% else %}
                <p class="warning">⚠ Non compatible avec Google Discover</p>
                {% if not results.discover_compatibility.has_large_images %}
                <p>Aucune image n'atteint la largeur minimale requise ({{ results.discover_compatibility.minimum_width_required }}px)</p>
                <p>Pour être compatible, assurez-vous d'avoir au moins une image de largeur supérieure ou égale à {{ results.discover_compatibility.minimum_width_required }}px.</p>
                {% endif %}
                {% endif %}
            </div>
            
            <div class="result-section">
                <h3>Les 3 plus grandes images</h3>
                <div class="card-grid">
                    {% for img in results.largest_images %}
                    <div class="card">
                        <a href="{{ img.url }}" target="_blank">
                            <img src="{{ img.url }}" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22150%22><rect width=%22200%22 height=%22150%22 fill=%22%23ddd%22/><text x=%22100%22 y=%2275%22 text-anchor=%22middle%22 fill=%22%23666%22>Image non disponible</text></svg>'">
                        </a>
                        <div class="card-content">
                            <div class="metadata">
                                <span>{{ img.width }} × {{ img.height }}</span>
                                <span>{{ (img.width * img.height)|int }} px²</span>
                            </div>
                            <div>
                                <a href="{{ img.url }}" target="_blank" style="font-size: 12px; word-break: break-all;">{{ img.url }}</a>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
        {% endif %}
        
        <footer>
            <p>© 2025 Analyseur d'Images pour Google Discover</p>
        </footer>
    </div>
</body>
</html>
'''

@app.route('/')
def home():
    return render_template_string(HOME_TEMPLATE)

@app.route('/analyze', methods=['POST'])
def analyze_form():
    url = request.form.get('url', '')
    logger.info(f"Nouvelle requête d'analyse reçue via formulaire : {url}")
    
    if not url:
        return render_template_string(HOME_TEMPLATE, error="URL manquante dans la requête")
        
    if not is_valid_url(url):
        return render_template_string(HOME_TEMPLATE, error=f"Format d'URL invalide : {url}")
    
    results, error = analyze_url(url)
    
    if error:
        return render_template_string(HOME_TEMPLATE, error=f"Erreur lors de l'analyse : {error}")
    
    logger.info("Analyse terminée avec succès")
    return render_template_string(HOME_TEMPLATE, results=results)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001) 