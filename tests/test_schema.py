import unittest

from pydantic import BaseModel

from creel.extract.schema import retry_prompt, validate


class Product(BaseModel):
    title: str
    price: float


class TestValidate(unittest.TestCase):
    def test_no_schema_passes_through(self):
        data = {"anything": "goes"}
        result, err = validate(data, None)
        self.assertEqual(result, data)
        self.assertIsNone(err)

    def test_valid_data_passes(self):
        result, err = validate({"title": "Widget", "price": 9.99}, Product)
        self.assertIsNone(err)
        self.assertEqual(result, {"title": "Widget", "price": 9.99})

    def test_missing_field_fails(self):
        result, err = validate({"title": "Widget"}, Product)
        self.assertIsNone(result)
        self.assertIsNotNone(err)

    def test_wrong_type_fails(self):
        result, err = validate({"title": "Widget", "price": "not-a-number"}, Product)
        self.assertIsNone(result)
        self.assertIsNotNone(err)

    def test_none_data_fails_with_schema(self):
        result, err = validate(None, Product)
        self.assertIsNone(result)
        self.assertIsNotNone(err)


class TestRetryPrompt(unittest.TestCase):
    def test_includes_original_and_error(self):
        prompt = retry_prompt("extract the product", "field 'price' required")
        self.assertIn("extract the product", prompt)
        self.assertIn("field 'price' required", prompt)


if __name__ == "__main__":
    unittest.main()
