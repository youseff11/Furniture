from django import forms
from .models import Product, ProductVariant

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        # أضفنا 'is_new_arrival' هنا لكي يظهر لك في صفحة manage_product.html
        fields = ['name', 'category', 'description', 'price', 'discount_price', 'is_new_arrival']
        
        # إضافة تنسيقات (Bootstrap) لتبدو الخانة بشكل جيد
        widgets = {
            'is_new_arrival': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'description': forms.Textarea(attrs={'rows': 4, 'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'price': forms.NumberInput(attrs={'class': 'form-control'}),
            'discount_price': forms.NumberInput(attrs={'class': 'form-control'}),
        }

class VariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = ['color_name', 'color_code', 'variant_image']