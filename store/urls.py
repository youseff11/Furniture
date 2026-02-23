from django.urls import path 
from . import views

urlpatterns = [
    # --- الصفحات العامة ---
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('logout/', views.logout_view, name='logout'),
    path('about/', views.about_view, name='about'),
    path('contact/', views.contact_view, name='contact'), 
    path('offers/', views.offers_view, name='offers'),
    
    # --- لوحة تحكم المسؤول (Dashboard) ---
    # هذا الرابط مخصص للسوبر يوزر فقط كما صممنا في الـ views
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('dashboard/add-product/', views.add_product, name='add_product'),
    path('dashboard/delete-product/<int:pk>/', views.delete_product, name='delete_product'),
    path('dashboard/edit-product/<int:pk>/', views.edit_product, name='edit_product'),
    path('order/update/<int:order_id>/', views.update_order_status, name='update_order_status'),
    path('reset-orders/', views.reset_orders, name='reset_orders'),
    path('admin-dashboard/update-item/<int:item_id>/', views.update_item_quantity, name='update_item_quantity'),
    path('admin-dashboard/apply-discount/<int:order_id>/', views.apply_order_discount, name='apply_order_discount'),

    # --- المتجر والمنتجات ---
    path('shop/', views.shop_view, name='shop'),     
    path('shop/<slug:category_slug>/', views.shop_view, name='shop_by_category'),
    path('product/<int:id>/', views.product_detail, name='product_detail'),

    # --- عربة التسوق (Cart) ---
    path('cart/', views.cart_view, name='cart_view'),
    
    # إضافة للمنتج
    path('add-to-cart/<int:product_id>/', views.add_to_cart, name='add_to_cart'),
    
    # التعامل مع مفاتيح السلة النصية (مثل 1_Black)
    path('remove-from-cart/<str:item_key>/', views.remove_from_cart, name='remove_from_cart'), 
    path('cart/update/<str:item_key>/<str:action>/', views.update_cart, name='update_cart'),

    # --- إتمام الطلب ---
    path('checkout/', views.checkout, name='checkout'),

    path('policies/', views.policies, name='policies'),
]