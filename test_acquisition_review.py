import unittest
from acquisition_review import review

class AcquisitionReviewTests(unittest.TestCase):
    def rows(self):
        return [{'scenario':'test','competitors':c,'offset_ms':o,'strategy':s,'outcome':'verified'} for c in (0,1,3) for o,s in ((10,'scheduled_retry'),(20,'scheduled_retry'),(40,'scheduled'))]
    def test_ties_and_missing_groups_do_not_qualify(self):
        self.assertTrue(all(d['decision']=='do_not_promote' for d in review(self.rows())['decisions']))
        self.assertTrue(all(d['decision']=='do_not_promote' for d in review(self.rows()[:1])['decisions']))
    def test_improvement_still_requires_more_validation(self):
        rows=self.rows()
        for row in rows:
            if row['competitors']==3 and row['offset_ms']==40:
                row['outcome']='competitor_capture'
        self.assertTrue(all(d['decision']=='larger_holdout_required' for d in review(rows)['decisions']))
    def test_clean_regression_blocks_candidate(self):
        rows=self.rows()
        for row in rows:
            if row['competitors']==0 and row['offset_ms']==10:
                row['outcome']='unresolved'
            if row['competitors']==3 and row['offset_ms']==40:
                row['outcome']='competitor_capture'
        self.assertEqual(review(rows)['decisions'][0]['decision'],'do_not_promote')
