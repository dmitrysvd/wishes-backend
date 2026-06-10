from app.schemas import WishReadSchema


def test_wish_read_schema_empty_image():
    # Coverage for line 78 in schemas.py (make_image_url)
    assert WishReadSchema.make_image_url('') is None
    assert WishReadSchema.make_image_url(None) is None  # type: ignore
    assert WishReadSchema.make_image_url('img.jpg') == '/media/wish_images/img.jpg'
