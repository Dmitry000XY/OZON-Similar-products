from ozon_similar_products.preprocessing.clean_events import EventCleaner
from ozon_similar_products.preprocessing.build_sessions import SessionBuilder
from ozon_similar_products.features.item_popularity import ItemPopularityBuilder
from ozon_similar_products.retrieval.build_pairs import ItemPairBuilder
from ozon_similar_products.retrieval.aggregate_pairs import PairAggregator
from ozon_similar_products.retrieval.update_strategy import FullRetrainStrategy
from ozon_similar_products.retrieval.scoring import CoVisitationScorer
from ozon_similar_products.retrieval.topk import TopKSelector
from ozon_similar_products.output.writers import RecommendationWriter
from ozon_similar_products.output.lookup import SimilarItemsLookup
from ozon_similar_products.pipeline.run_mvp import run_mvp_pipeline


def test_mvp_interfaces_importable() -> None:
    assert EventCleaner is not None
    assert SessionBuilder is not None
    assert ItemPopularityBuilder is not None
    assert ItemPairBuilder is not None
    assert PairAggregator is not None
    assert FullRetrainStrategy is not None
    assert CoVisitationScorer is not None
    assert TopKSelector is not None
    assert RecommendationWriter is not None
    assert SimilarItemsLookup is not None
    assert run_mvp_pipeline is not None
