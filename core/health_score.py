import logging

logger = logging.getLogger(__name__)


class FundamentalScorer:
    def __init__(self):
        self.scores = {}

    def score_companies(self):
        """
        Placeholder fundamental scoring.

        Until the fundamental-data pipeline is rebuilt,
        all companies are treated as neutral.
        """
        self.scores = {}

        logger.info(
            "Fundamental scoring is currently disabled. "
            "Neutral score will be used."
        )

    def get_score(self, symbol: str) -> int:
        """
        Returns a neutral score until the
        fundamental-data pipeline is implemented.
        """
        return self.scores.get(symbol, 50)


scorer = FundamentalScorer()