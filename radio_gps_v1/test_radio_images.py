import base64
import copy
import io
import importlib.util
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from PIL import Image
from serial_link import receive_serial_packets, send_serial_packet
from test_serial_link import Port
from radio_images import save_preview, attach_preview, MAX_JPEG_BYTES


def image_packet():
    p=json.loads(Path(__file__).with_name('sample_packet_no_gps.json').read_text())
    stream=io.BytesIO();Image.new('RGB',(160,120),'blue').save(stream,format='JPEG')
    p['thumbnail']=None
    p['thumbnail_jpeg_b64']=base64.b64encode(stream.getvalue()).decode('ascii')
    return p


class ImageTests(unittest.TestCase):
    def test_invalid_images_do_not_write_files(self):
        cases=['garbage!',base64.b64encode(b'x'*4097).decode('ascii')]
        for size,fmt in [((161,120),'JPEG'),((160,120),'PNG')]:
            stream=io.BytesIO();Image.new('RGB',size,'red').save(stream,format=fmt)
            cases.append(base64.b64encode(stream.getvalue()).decode('ascii'))
        for encoded in cases:
            with self.subTest(encoded=encoded[:20]), tempfile.TemporaryDirectory() as tmp:
                p=image_packet();p['thumbnail_jpeg_b64']=encoded
                with self.assertRaises(ValueError):save_preview(p,tmp)
                self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_image_arrives_before_gui_event_and_exports(self):
        from details_panel import selected_data
        a,b=Port(),Port();a.peer=b;b.peer=a;incoming=queue.Queue();stop=threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            t=threading.Thread(target=receive_serial_packets,args=(incoming,'TEST'),
                kwargs={'log_directory':Path(tmp)/'logs','image_directory':tmp,
                        'stop_event':stop,'port_factory':lambda *a,**kw:b},daemon=True)
            t.start();incoming.get(timeout=2)
            try:
                p=image_packet();response=send_serial_packet(a,p,timeout=2)
                self.assertEqual(response['status'],'accepted')
                kind,d=incoming.get(timeout=2);self.assertEqual(kind,'detection')
                path=Path(tmp)/d.image_path
                self.assertTrue(path.is_file());self.assertEqual(path.suffix,'.jpg')
                with Image.open(path) as image:self.assertEqual(image.size,(160,120))
                self.assertEqual(selected_data(d,{'Packet ID'})['packet_id'],p['packet_id'])
                record=json.loads(next((Path(tmp)/'logs').glob('*.jsonl')).read_text())['packet']
                self.assertNotIn('thumbnail_jpeg_b64',record)
                self.assertEqual(record['thumbnail'],d.image_path)
                bad=copy.deepcopy(p);bad['packet_id']=99;bad['thumbnail_jpeg_b64']='bad!'
                self.assertEqual(send_serial_packet(a,bad,timeout=2)['status'],'rejected')
                p['packet_id']=100;p.pop('thumbnail_jpeg_b64')
                self.assertEqual(send_serial_packet(a,p,timeout=2)['status'],'accepted')
            finally:
                stop.set();t.join(2)

    @unittest.skipUnless(importlib.util.find_spec('cv2'), 'OpenCV is used only by Pi camera sender')
    def test_real_opencv_compression_and_original_unchanged(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            rng=np.random.default_rng(123)
            path=Path(tmp)/'snapshot.jpg'
            self.assertTrue(cv2.imwrite(str(path),rng.integers(0,256,(1088,1456,3),dtype=np.uint8)))
            p={'packet_id':1,'thumbnail':path.name}
            out=attach_preview(p,tmp)
            self.assertEqual(p['thumbnail'],path.name)
            self.assertIsNone(out['thumbnail'])
            self.assertLessEqual(len(base64.b64decode(out['thumbnail_jpeg_b64'])),MAX_JPEG_BYTES)
            result=save_preview(out,tmp)
            self.assertTrue((Path(tmp)/result['thumbnail']).is_file())

if __name__=='__main__':unittest.main()
