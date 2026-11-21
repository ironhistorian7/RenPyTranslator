"""Cached Korean history is optional evidence, never a dependency between workers."""
import unittest
from source_context import SourceContext


class KoreanHistoryTests(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(id=str(i),source=text,kind='dialogue',usage='dialogue',file='test',block='one')
                   for i,text in enumerate(['前の文','今回の文','次の文'])]
        self.cfg={'engine':'rpgmaker','source_language':'japanese','num_ctx':16384}
        self.index=SourceContext(self.rows)

    def test_only_committed_valid_history_is_substituted(self):
        known={'0':dict(source='前の文',text='앞 문장입니다.'),'2':dict(source='次の文',text='뒤 문장입니다.')}
        passage=self.index.context([self.rows[1]],self.cfg,known=known)['source_passage']
        self.assertEqual(passage[0]['text'],'앞 문장입니다.')
        self.assertEqual(passage[1],{'target_id':'0'})
        self.assertEqual(passage[2]['text'],'次の文')
        self.assertEqual(self.rows[0]['source'],'前の文')

    def test_absent_incomplete_or_wrong_source_history_does_not_block_or_leak(self):
        for saved in (None,dict(source='前の文',text='한국어日本語',status='partial_translation'),
                      dict(source='other',text='다른 문장입니다.'),
                      dict(source='前の文',text='미완료',_rpg_residual_language=True)):
            known={'0':saved} if saved is not None else {}
            passage=self.index.context([self.rows[1]],self.cfg,known=known)['source_passage']
            self.assertEqual(passage[0]['text'],'前の文')

    def test_english_and_renpy_keep_original_context(self):
        known={'0':dict(source='前の文',text='앞 문장입니다.')}
        for cfg in (dict(self.cfg,source_language='english'),dict(self.cfg,engine='renpy')):
            passage=self.index.context([self.rows[1]],cfg,known=known)['source_passage']
            self.assertEqual(passage[0]['text'],'前の文')


if __name__=='__main__':unittest.main()
