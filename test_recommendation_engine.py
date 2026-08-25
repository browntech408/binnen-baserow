import unittest
from recommendation_engine import RecommendationEngine, compute_quality_score, cosine_similarity, extract_style_vector

class TestRecommendationEngine(unittest.TestCase):

    def setUp(self):
        self.sample_products = [
            {
                "id": 1,
                "field_7347": "Modern Minimalist Sofa",
                "field_7363": [{"id": 1, "value": "Banken"}],
                "field_7364": [{"id": 1, "value": "3-zitsbank"}],
                "field_7376": [{"id": 1, "value": "Montis"}],
                "field_7378": 90, # Scandinavian
                "field_7379": 85, # Japandi
                "field_7380": 95, # Minimalist
                "field_7358": [{"url": "http://example.com/hero.jpg"}],
                "field_7359": [{"url": "http://example.com/life.jpg"}],
                "field_7362": "Prachtige modern minimalistische bank.",
                "field_7425": "gid://shopify/Product/1001", # Woonbloq
                "field_7407": "gid://shopify/Product/2001", # Binnen
            },
            {
                "id": 2,
                "field_7347": "Japandi Coffee Table",
                "field_7363": [{"id": 3, "value": "Tafels"}],
                "field_7364": [{"id": 15, "value": "Salontafel"}],
                "field_7376": [{"id": 1, "value": "Montis"}],
                "field_7378": 88, # Scandinavian
                "field_7379": 92, # Japandi
                "field_7380": 90, # Minimalist
                "field_7358": [{"url": "http://example.com/hero2.jpg"}],
                "field_7425": "gid://shopify/Product/1002",
                "field_7407": "gid://shopify/Product/2002",
            },
            {
                "id": 3,
                "field_7347": "Classic Oak Dining Table",
                "field_7363": [{"id": 3, "value": "Tafels"}],
                "field_7364": [{"id": 8, "value": "Eettafel"}],
                "field_7376": [{"id": 7, "value": "Bert Plantagie"}],
                "field_7390": 95, # Landelijk
                "field_7358": [{"url": "http://example.com/hero3.jpg"}],
                "field_7425": "gid://shopify/Product/1003",
                "field_7407": "gid://shopify/Product/2003",
            },
            {
                "id": 4,
                "field_7347": "Minimalist Dining Chair",
                "field_7363": [{"id": 2, "value": "Stoelen"}],
                "field_7364": [{"id": 7, "value": "Eetkamerstoelen"}],
                "field_7376": [{"id": 7, "value": "Bert Plantagie"}],
                "field_7380": 90, # Minimalist
                "field_7358": [{"url": "http://example.com/hero4.jpg"}],
                "field_7425": "gid://shopify/Product/1004",
                "field_7407": "gid://shopify/Product/2004",
            }
        ]
        self.engine = RecommendationEngine(self.sample_products)

    def test_quality_score_calculation(self):
        score = compute_quality_score(self.sample_products[0])
        self.assertGreaterEqual(score, 60.0)

    def test_style_vector_cosine_similarity(self):
        v1 = extract_style_vector(self.sample_products[0])
        v2 = extract_style_vector(self.sample_products[1])
        sim = cosine_similarity(v1, v2)
        self.assertGreater(sim, 0.90)

    def test_recommendation_matching(self):
        recs = self.engine.get_recommendations(self.sample_products[0], top_k=2)
        self.assertEqual(len(recs), 2)
        # Top recommendation for Sofa should be Coffee Table (complementary match)
        top_rec_id = recs[0][0]["id"]
        self.assertEqual(top_rec_id, 2)

    def test_fbt_bundle_matching(self):
        bundles = self.engine.get_fbt_bundles(self.sample_products[2], top_k=1)
        self.assertEqual(len(bundles), 1)
        # Bundle for Dining Table should be Dining Chair
        self.assertEqual(bundles[0]["id"], 4)

if __name__ == "__main__":
    unittest.main()
