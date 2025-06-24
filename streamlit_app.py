import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import json
import hashlib
from typing import Dict, List, Tuple, Optional
import uuid
import time
import base64
from io import BytesIO
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import time
from functools import lru_cache
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# For PDF generation
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

# Optional: Import for real Claude AI integration
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

# Page configuration
st.set_page_config(
    page_title="Complete Enterprise AWS Migration Platform",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded"
)

class AWSPricingManager:
    """Comprehensive AWS pricing manager with full API integration"""
    
    def __init__(self, region='us-east-1'):
        self.region = region
        self.pricing_client = None
        self.ec2_client = None
        self.cache = {}
        self.cache_ttl = 3600
        self.last_cache_update = {}
        self._init_clients()
    
    def _init_clients(self):
        """Initialize AWS clients using Streamlit secrets"""
        try:
            aws_access_key = None
            aws_secret_key = None
            aws_region = self.region
            
            try:
                if hasattr(st, 'secrets') and 'aws' in st.secrets:
                    aws_access_key = st.secrets["aws"]["access_key_id"]
                    aws_secret_key = st.secrets["aws"]["secret_access_key"]
                    aws_region = st.secrets["aws"].get("region", self.region)
                    
                    st.success("🔑 AWS credentials loaded from secrets.toml")
                    
                    self.pricing_client = boto3.client(
                        'pricing',
                        region_name='us-east-1',
                        aws_access_key_id=aws_access_key,
                        aws_secret_access_key=aws_secret_key
                    )
                    self.ec2_client = boto3.client(
                        'ec2',
                        region_name=aws_region,
                        aws_access_key_id=aws_access_key,
                        aws_secret_access_key=aws_secret_key
                    )
                else:
                    st.info("💡 Using default AWS credential chain")
                    self.pricing_client = boto3.client('pricing', region_name='us-east-1')
                    self.ec2_client = boto3.client('ec2', region_name=aws_region)
                    
            except KeyError as e:
                st.warning(f"⚠️ AWS secrets configuration incomplete: {str(e)}")
                self.pricing_client = None
                self.ec2_client = None
                return
            
            try:
                self.pricing_client.describe_services(MaxResults=1)
                st.success("✅ AWS Pricing API connection successful")
            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == 'UnauthorizedOperation':
                    st.error("❌ AWS credentials valid but missing pricing permissions")
                else:
                    st.warning(f"⚠️ AWS API error: {str(e)}")
                self.pricing_client = None
                self.ec2_client = None
                
        except Exception as e:
            st.error(f"❌ Error initializing AWS clients: {str(e)}")
            self.pricing_client = None
            self.ec2_client = None
    
    def _is_cache_valid(self, key):
        if key not in self.cache or key not in self.last_cache_update:
            return False
        return (time.time() - self.last_cache_update[key]) < self.cache_ttl
    
    def _update_cache(self, key, value):
        self.cache[key] = value
        self.last_cache_update[key] = time.time()
    
    def get_ec2_pricing(self, instance_type, region=None):
        """Get EC2 instance pricing"""
        if not self.pricing_client:
            return self._get_fallback_ec2_pricing(instance_type)
        
        cache_key = f"ec2_{instance_type}_{region or self.region}"
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]
        
        try:
            response = self.pricing_client.get_products(
                ServiceCode='AmazonEC2',
                MaxResults=1,
                Filters=[
                    {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type},
                    {'Type': 'TERM_MATCH', 'Field': 'operatingSystem', 'Value': 'Linux'},
                    {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': self._get_location_name(region or self.region)},
                    {'Type': 'TERM_MATCH', 'Field': 'tenancy', 'Value': 'Shared'},
                    {'Type': 'TERM_MATCH', 'Field': 'preInstalledSw', 'Value': 'NA'}
                ]
            )
            
            if response['PriceList']:
                price_data = json.loads(response['PriceList'][0])
                terms = price_data['terms']['OnDemand']
                
                for term_key, term_value in terms.items():
                    for price_dimension_key, price_dimension in term_value['priceDimensions'].items():
                        if 'USD' in price_dimension['pricePerUnit']:
                            hourly_price = float(price_dimension['pricePerUnit']['USD'])
                            self._update_cache(cache_key, hourly_price)
                            return hourly_price
            
            return self._get_fallback_ec2_pricing(instance_type)
            
        except Exception as e:
            st.warning(f"Error fetching EC2 pricing for {instance_type}: {str(e)}")
            return self._get_fallback_ec2_pricing(instance_type)
    
    def get_dms_pricing(self, instance_type, region=None):
        """Get DMS instance pricing"""
        if not self.pricing_client:
            return self._get_fallback_dms_pricing(instance_type)
        
        cache_key = f"dms_{instance_type}_{region or self.region}"
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]
        
        try:
            response = self.pricing_client.get_products(
                ServiceCode='AWSDataMigrationSvc',
                MaxResults=1,
                Filters=[
                    {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type},
                    {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': self._get_location_name(region or self.region)}
                ]
            )
            
            if response['PriceList']:
                price_data = json.loads(response['PriceList'][0])
                terms = price_data['terms']['OnDemand']
                
                for term_key, term_value in terms.items():
                    for price_dimension_key, price_dimension in term_value['priceDimensions'].items():
                        if 'USD' in price_dimension['pricePerUnit']:
                            hourly_price = float(price_dimension['pricePerUnit']['USD'])
                            self._update_cache(cache_key, hourly_price)
                            return hourly_price
            
            return self._get_fallback_dms_pricing(instance_type)
            
        except Exception as e:
            st.warning(f"Error fetching DMS pricing for {instance_type}: {str(e)}")
            return self._get_fallback_dms_pricing(instance_type)
    
    def get_s3_pricing(self, storage_class, region=None):
        """Get S3 storage pricing"""
        if not self.pricing_client:
            return self._get_fallback_s3_pricing(storage_class)
        
        cache_key = f"s3_{storage_class}_{region or self.region}"
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]
        
        try:
            storage_class_mapping = {
                "Standard": "General Purpose",
                "Standard-IA": "Infrequent Access",
                "One Zone-IA": "One Zone - Infrequent Access",
                "Glacier Instant Retrieval": "Amazon Glacier Instant Retrieval",
                "Glacier Flexible Retrieval": "Amazon Glacier Flexible Retrieval",
                "Glacier Deep Archive": "Amazon Glacier Deep Archive"
            }
            
            aws_storage_class = storage_class_mapping.get(storage_class, "General Purpose")
            
            response = self.pricing_client.get_products(
                ServiceCode='AmazonS3',
                MaxResults=1,
                Filters=[
                    {'Type': 'TERM_MATCH', 'Field': 'storageClass', 'Value': aws_storage_class},
                    {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': self._get_location_name(region or self.region)},
                    {'Type': 'TERM_MATCH', 'Field': 'volumeType', 'Value': 'Standard'}
                ]
            )
            
            if response['PriceList']:
                price_data = json.loads(response['PriceList'][0])
                terms = price_data['terms']['OnDemand']
                
                for term_key, term_value in terms.items():
                    for price_dimension_key, price_dimension in term_value['priceDimensions'].items():
                        if 'USD' in price_dimension['pricePerUnit']:
                            gb_price = float(price_dimension['pricePerUnit']['USD'])
                            self._update_cache(cache_key, gb_price)
                            return gb_price
            
            return self._get_fallback_s3_pricing(storage_class)
            
        except Exception as e:
            st.warning(f"Error fetching S3 pricing for {storage_class}: {str(e)}")
            return self._get_fallback_s3_pricing(storage_class)
    
    def get_snowball_pricing(self, device_type):
        """Get Snowball device pricing"""
        pricing = {
            "snowcone": {"device_fee": 60, "data_transfer": 0.0, "days_included": 5},
            "snowball_edge_storage": {"device_fee": 300, "data_transfer": 0.0, "days_included": 10},
            "snowball_edge_compute": {"device_fee": 400, "data_transfer": 0.0, "days_included": 10},
            "snowmobile": {"device_fee": 0.005, "data_transfer": 0.0, "days_included": 0}
        }
        return pricing.get(device_type, pricing["snowball_edge_storage"])
    
    def get_data_transfer_pricing(self, region=None):
        """Get data transfer pricing"""
        if not self.pricing_client:
            return 0.09
        
        cache_key = f"transfer_{region or self.region}"
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]
        
        try:
            response = self.pricing_client.get_products(
                ServiceCode='AmazonEC2',
                MaxResults=1,
                Filters=[
                    {'Type': 'TERM_MATCH', 'Field': 'transferType', 'Value': 'AWS Outbound'},
                    {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': self._get_location_name(region or self.region)}
                ]
            )
            
            if response['PriceList']:
                price_data = json.loads(response['PriceList'][0])
                terms = price_data['terms']['OnDemand']
                
                for term_key, term_value in terms.items():
                    for price_dimension_key, price_dimension in term_value['priceDimensions'].items():
                        if 'USD' in price_dimension['pricePerUnit']:
                            transfer_price = float(price_dimension['pricePerUnit']['USD'])
                            self._update_cache(cache_key, transfer_price)
                            return transfer_price
            
            return 0.09
            
        except Exception as e:
            st.warning(f"Error fetching data transfer pricing: {str(e)}")
            return 0.09
    
    def get_direct_connect_pricing(self, bandwidth_mbps, region=None):
        """Get Direct Connect pricing"""
        if not self.pricing_client:
            return self._get_fallback_dx_pricing(bandwidth_mbps)
        
        cache_key = f"dx_{bandwidth_mbps}_{region or self.region}"
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]
        
        try:
            if bandwidth_mbps >= 10000:
                port_speed = "10Gbps"
            elif bandwidth_mbps >= 1000:
                port_speed = "1Gbps"
            else:
                port_speed = "100Mbps"
            
            response = self.pricing_client.get_products(
                ServiceCode='AWSDirectConnect',
                MaxResults=1,
                Filters=[
                    {'Type': 'TERM_MATCH', 'Field': 'portSpeed', 'Value': port_speed},
                    {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': self._get_location_name(region or self.region)}
                ]
            )
            
            if response['PriceList']:
                price_data = json.loads(response['PriceList'][0])
                terms = price_data['terms']['OnDemand']
                
                for term_key, term_value in terms.items():
                    for price_dimension_key, price_dimension in term_value['priceDimensions'].items():
                        if 'USD' in price_dimension['pricePerUnit']:
                            monthly_price = float(price_dimension['pricePerUnit']['USD'])
                            hourly_price = monthly_price / (24 * 30)
                            self._update_cache(cache_key, hourly_price)
                            return hourly_price
            
            return self._get_fallback_dx_pricing(bandwidth_mbps)
            
        except Exception as e:
            st.warning(f"Error fetching Direct Connect pricing: {str(e)}")
            return self._get_fallback_dx_pricing(bandwidth_mbps)
    
    def _get_location_name(self, region):
        """Map AWS region codes to location names"""
        location_mapping = {
            'us-east-1': 'US East (N. Virginia)',
            'us-east-2': 'US East (Ohio)',
            'us-west-1': 'US West (N. California)',
            'us-west-2': 'US West (Oregon)',
            'eu-west-1': 'Europe (Ireland)',
            'eu-central-1': 'Europe (Frankfurt)',
            'ap-southeast-1': 'Asia Pacific (Singapore)',
            'ap-northeast-1': 'Asia Pacific (Tokyo)',
            'ap-south-1': 'Asia Pacific (Mumbai)',
            'sa-east-1': 'South America (Sao Paulo)'
        }
        return location_mapping.get(region, 'US East (N. Virginia)')
    
    def _get_fallback_ec2_pricing(self, instance_type):
        """Fallback EC2 pricing"""
        fallback_prices = {
            "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384,
            "m5.4xlarge": 0.768, "m5.8xlarge": 1.536,
            "c5.2xlarge": 0.34, "c5.4xlarge": 0.68, "c5.9xlarge": 1.53,
            "r5.2xlarge": 0.504, "r5.4xlarge": 1.008
        }
        return fallback_prices.get(instance_type, 0.10)
    
    def _get_fallback_dms_pricing(self, instance_type):
        """Fallback DMS pricing"""
        fallback_prices = {
            "dms.t3.micro": 0.020, "dms.t3.small": 0.040, "dms.t3.medium": 0.080,
            "dms.t3.large": 0.160, "dms.c5.large": 0.192, "dms.c5.xlarge": 0.384,
            "dms.c5.2xlarge": 0.768, "dms.c5.4xlarge": 1.536,
            "dms.r5.large": 0.252, "dms.r5.xlarge": 0.504,
            "dms.r5.2xlarge": 1.008, "dms.r5.4xlarge": 2.016
        }
        return fallback_prices.get(instance_type, 0.20)
    
    def _get_fallback_s3_pricing(self, storage_class):
        """Fallback S3 pricing"""
        fallback_prices = {
            "Standard": 0.023, "Standard-IA": 0.0125, "One Zone-IA": 0.01,
            "Glacier Instant Retrieval": 0.004, "Glacier Flexible Retrieval": 0.0036,
            "Glacier Deep Archive": 0.00099
        }
        return fallback_prices.get(storage_class, 0.023)
    
    def _get_fallback_dx_pricing(self, bandwidth_mbps):
        """Fallback Direct Connect pricing"""
        if bandwidth_mbps >= 10000:
            return 1.55
        elif bandwidth_mbps >= 1000:
            return 0.30
        else:
            return 0.03
    
    def get_comprehensive_pricing(self, instance_type, storage_class, region=None, bandwidth_mbps=1000):
        """Get comprehensive pricing for all services"""
        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    'ec2': executor.submit(self.get_ec2_pricing, instance_type, region),
                    's3': executor.submit(self.get_s3_pricing, storage_class, region),
                    'transfer': executor.submit(self.get_data_transfer_pricing, region),
                    'dx': executor.submit(self.get_direct_connect_pricing, bandwidth_mbps, region)
                }
                
                pricing = {}
                for key, future in futures.items():
                    try:
                        pricing[key] = future.result(timeout=10)
                    except Exception as e:
                        st.warning(f"Timeout fetching {key} pricing: {str(e)}")
                        if key == 'ec2':
                            pricing[key] = self._get_fallback_ec2_pricing(instance_type)
                        elif key == 's3':
                            pricing[key] = self._get_fallback_s3_pricing(storage_class)
                        elif key == 'transfer':
                            pricing[key] = 0.09
                        elif key == 'dx':
                            pricing[key] = self._get_fallback_dx_pricing(bandwidth_mbps)
                
                return pricing
                
        except Exception as e:
            st.error(f"Error in comprehensive pricing fetch: {str(e)}")
            return {
                'ec2': self._get_fallback_ec2_pricing(instance_type),
                's3': self._get_fallback_s3_pricing(storage_class),
                'transfer': 0.09,
                'dx': self._get_fallback_dx_pricing(bandwidth_mbps)
            }

class EnhancedMigrationCalculator:
    """Complete migration calculator with all original features plus multi-service support"""
    
    def __init__(self):
        # Original DataSync performance data
        self.instance_performance = {
            "m5.large": {"cpu": 2, "memory": 8, "network": 750, "baseline_throughput": 150, "cost_hour": 0.096},
            "m5.xlarge": {"cpu": 4, "memory": 16, "network": 750, "baseline_throughput": 250, "cost_hour": 0.192},
            "m5.2xlarge": {"cpu": 8, "memory": 32, "network": 1000, "baseline_throughput": 400, "cost_hour": 0.384},
            "m5.4xlarge": {"cpu": 16, "memory": 64, "network": 2000, "baseline_throughput": 600, "cost_hour": 0.768},
            "m5.8xlarge": {"cpu": 32, "memory": 128, "network": 4000, "baseline_throughput": 1000, "cost_hour": 1.536},
            "c5.2xlarge": {"cpu": 8, "memory": 16, "network": 2000, "baseline_throughput": 500, "cost_hour": 0.34},
            "c5.4xlarge": {"cpu": 16, "memory": 32, "network": 4000, "baseline_throughput": 800, "cost_hour": 0.68},
            "c5.9xlarge": {"cpu": 36, "memory": 72, "network": 10000, "baseline_throughput": 1500, "cost_hour": 1.53},
            "r5.2xlarge": {"cpu": 8, "memory": 64, "network": 2000, "baseline_throughput": 450, "cost_hour": 0.504},
            "r5.4xlarge": {"cpu": 16, "memory": 128, "network": 4000, "baseline_throughput": 700, "cost_hour": 1.008}
        }
        
        # DMS instance performance
        self.dms_performance = {
            "dms.t3.micro": {"cpu": 2, "memory": 1, "network": 2048, "throughput_mbps": 50, "cost_hour": 0.020},
            "dms.t3.small": {"cpu": 2, "memory": 2, "network": 5000, "throughput_mbps": 100, "cost_hour": 0.040},
            "dms.t3.medium": {"cpu": 2, "memory": 4, "network": 5000, "throughput_mbps": 200, "cost_hour": 0.080},
            "dms.t3.large": {"cpu": 2, "memory": 8, "network": 5000, "throughput_mbps": 400, "cost_hour": 0.160},
            "dms.c5.large": {"cpu": 2, "memory": 4, "network": 10000, "throughput_mbps": 600, "cost_hour": 0.192},
            "dms.c5.xlarge": {"cpu": 4, "memory": 8, "network": 10000, "throughput_mbps": 1200, "cost_hour": 0.384},
            "dms.c5.2xlarge": {"cpu": 8, "memory": 16, "network": 10000, "throughput_mbps": 2400, "cost_hour": 0.768},
            "dms.c5.4xlarge": {"cpu": 16, "memory": 32, "network": 10000, "throughput_mbps": 4800, "cost_hour": 1.536},
            "dms.r5.large": {"cpu": 2, "memory": 16, "network": 10000, "throughput_mbps": 800, "cost_hour": 0.252},
            "dms.r5.xlarge": {"cpu": 4, "memory": 32, "network": 10000, "throughput_mbps": 1600, "cost_hour": 0.504},
            "dms.r5.2xlarge": {"cpu": 8, "memory": 64, "network": 10000, "throughput_mbps": 3200, "cost_hour": 1.008},
            "dms.r5.4xlarge": {"cpu": 16, "memory": 128, "network": 10000, "throughput_mbps": 6400, "cost_hour": 2.016}
        }
        
        # Snowball family specifications
        self.snowball_specs = {
            "snowcone": {
                "capacity_tb": 0.008,
                "transfer_rate_gbps": 0.025,
                "weight_lbs": 4.5,
                "shipping_days": 4,
                "use_case": "Edge computing, small datasets"
            },
            "snowball_edge_storage": {
                "capacity_tb": 80,
                "transfer_rate_gbps": 1.0,
                "weight_lbs": 49.7,
                "shipping_days": 6,
                "use_case": "Large datasets, local processing"
            },
            "snowball_edge_compute": {
                "capacity_tb": 42,
                "transfer_rate_gbps": 1.0,
                "weight_lbs": 49.7,
                "shipping_days": 6,
                "use_case": "Edge computing with storage"
            }
        }
        
        # Original file size multipliers
        self.file_size_multipliers = {
            "< 1MB (Many small files)": 0.25,
            "1-10MB (Small files)": 0.45,
            "10-100MB (Medium files)": 0.70,
            "100MB-1GB (Large files)": 0.90,
            "> 1GB (Very large files)": 0.95
        }
        
        # Original compliance requirements
        self.compliance_requirements = {
            "SOX": {"encryption_required": True, "audit_trail": True, "data_retention": 7},
            "GDPR": {"encryption_required": True, "data_residency": True, "right_to_delete": True},
            "HIPAA": {"encryption_required": True, "access_logging": True, "data_residency": True},
            "PCI-DSS": {"encryption_required": True, "network_segmentation": True, "access_control": True},
            "SOC2": {"encryption_required": True, "monitoring": True, "access_control": True},
            "ISO27001": {"risk_assessment": True, "documentation": True, "continuous_monitoring": True},
            "FedRAMP": {"encryption_required": True, "continuous_monitoring": True, "incident_response": True},
            "FISMA": {"encryption_required": True, "access_control": True, "audit_trail": True}
        }
        
        # Original geographic latency matrix
        self.geographic_latency = {
            "San Jose, CA": {"us-west-1": 15, "us-west-2": 25, "us-east-1": 70, "us-east-2": 65},
            "San Antonio, TX": {"us-west-1": 45, "us-west-2": 50, "us-east-1": 35, "us-east-2": 30},
            "New York, NY": {"us-west-1": 75, "us-west-2": 80, "us-east-1": 10, "us-east-2": 15},
            "Chicago, IL": {"us-west-1": 60, "us-west-2": 65, "us-east-1": 25, "us-east-2": 20},
            "Dallas, TX": {"us-west-1": 40, "us-west-2": 45, "us-east-1": 35, "us-east-2": 30},
            "Los Angeles, CA": {"us-west-1": 20, "us-west-2": 15, "us-east-1": 75, "us-east-2": 70},
            "Atlanta, GA": {"us-west-1": 65, "us-west-2": 70, "us-east-1": 15, "us-east-2": 20},
            "London, UK": {"us-west-1": 150, "us-west-2": 155, "us-east-1": 80, "us-east-2": 85},
            "Frankfurt, DE": {"us-west-1": 160, "us-west-2": 165, "us-east-1": 90, "us-east-2": 95},
            "Tokyo, JP": {"us-west-1": 120, "us-west-2": 115, "us-east-1": 180, "us-east-2": 185},
            "Sydney, AU": {"us-west-1": 170, "us-west-2": 165, "us-east-1": 220, "us-east-2": 225}
        }
        
        # Enhanced network patterns
        self.network_patterns = {
            "direct_connect_dedicated": {
                "max_bandwidth_gbps": 100,
                "latency_ms": 1,
                "availability": 99.95,
                "setup_time_days": 30,
                "monthly_cost_per_gbps": 300,
                "efficiency": 0.95
            },
            "direct_connect_hosted": {
                "max_bandwidth_gbps": 10,
                "latency_ms": 2,
                "availability": 99.9,
                "setup_time_days": 14,
                "monthly_cost_per_gbps": 100,
                "efficiency": 0.90
            },
            "site_to_site_vpn": {
                "max_bandwidth_gbps": 1.25,
                "latency_ms": 150,
                "availability": 99.95,
                "setup_time_days": 1,
                "monthly_cost_per_gbps": 45,
                "efficiency": 0.75
            },
            "transit_gateway": {
                "max_bandwidth_gbps": 50,
                "latency_ms": 1,
                "availability": 99.95,
                "setup_time_days": 1,
                "monthly_cost_per_gbps": 50,
                "efficiency": 0.85
            }
        }
        
        # Service compatibility matrix
        self.service_compatibility = {
            "datasync": {
                "data_types": ["files", "objects", "nfs", "smb"],
                "max_file_size": "5TB",
                "incremental_support": True,
                "bandwidth_efficiency": 0.85
            },
            "dms": {
                "data_types": ["databases", "cdc", "analytics"],
                "max_database_size": "64TB",
                "incremental_support": True,
                "bandwidth_efficiency": 0.75
            },
            "snowball": {
                "data_types": ["files", "objects", "archives"],
                "max_file_size": "5TB",
                "incremental_support": False,
                "bandwidth_efficiency": 1.0
            }
        }
        
        # Original database migration tools
        self.db_migration_tools = {
            "DMS": {
                "name": "Database Migration Service",
                "best_for": ["Homogeneous", "Heterogeneous", "Continuous Replication"],
                "data_size_limit": "Large (TB scale)",
                "downtime": "Minimal",
                "cost_factor": 1.0,
                "complexity": "Medium"
            },
            "DataSync": {
                "name": "AWS DataSync",
                "best_for": ["File Systems", "Object Storage", "Large Files"],
                "data_size_limit": "Very Large (PB scale)",
                "downtime": "None",
                "cost_factor": 0.8,
                "complexity": "Low"
            },
            "DMS+DataSync": {
                "name": "Hybrid DMS + DataSync",
                "best_for": ["Complex Workloads", "Mixed Data Types"],
                "data_size_limit": "Very Large",
                "downtime": "Low",
                "cost_factor": 1.3,
                "complexity": "High"
            },
            "Snowball Edge": {
                "name": "AWS Snowball Edge",
                "best_for": ["Limited Bandwidth", "Large Datasets"],
                "data_size_limit": "Very Large (100TB per device)",
                "downtime": "Medium",
                "cost_factor": 0.6,
                "complexity": "Low"
            }
        }
        
        self.pricing_manager = AWSPricingManager()
    
    def calculate_enterprise_throughput(self, instance_type, num_agents, file_size_category, 
                                        network_bw_mbps, latency, jitter, packet_loss, qos_enabled, 
                                        dedicated_bandwidth, real_world_mode=True, network_pattern="direct_connect_dedicated"):
        """Enhanced throughput calculation with network pattern support"""
        
        base_performance = self.instance_performance[instance_type]["baseline_throughput"]
        file_efficiency = self.file_size_multipliers[file_size_category]
        
        # Network impact calculations
        latency_factor = max(0.4, 1 - (latency - 5) / 500)
        jitter_factor = max(0.8, 1 - jitter / 100)
        packet_loss_factor = max(0.6, 1 - packet_loss / 10)
        qos_factor = 1.2 if qos_enabled else 1.0
        
        # Network pattern efficiency
        pattern_efficiency = self.network_patterns.get(network_pattern, {}).get("efficiency", 0.85)
        
        network_efficiency = latency_factor * jitter_factor * packet_loss_factor * qos_factor * pattern_efficiency
        
        # Real-world efficiency factors
        if real_world_mode:
            datasync_overhead = 0.75
            storage_io_factor = 0.6
            tcp_efficiency = 0.8
            s3_api_efficiency = 0.85
            filesystem_overhead = 0.9
            
            if instance_type == "m5.large":
                cpu_memory_factor = 0.7
            elif instance_type in ["m5.xlarge", "m5.2xlarge"]:
                cpu_memory_factor = 0.8
            else:
                cpu_memory_factor = 0.9
            
            concurrent_workload_factor = 0.85
            peak_hour_factor = 0.9
            error_handling_overhead = 0.95
            
            real_world_efficiency = (datasync_overhead * storage_io_factor * tcp_efficiency * 
                                   s3_api_efficiency * filesystem_overhead * cpu_memory_factor * 
                                   concurrent_workload_factor * peak_hour_factor * error_handling_overhead)
        else:
            real_world_efficiency = 0.95
        
        # Multi-agent scaling
        total_throughput = 0
        for i in range(num_agents):
            agent_efficiency = max(0.4, 1 - (i * 0.05))
            agent_throughput = (base_performance * file_efficiency * network_efficiency * 
                              real_world_efficiency * agent_efficiency)
            total_throughput += agent_throughput
        
        # Apply bandwidth limitation
        max_available_bandwidth = network_bw_mbps * (dedicated_bandwidth / 100)
        effective_throughput = min(total_throughput, max_available_bandwidth)
        
        theoretical_throughput = min(base_performance * file_efficiency * network_efficiency * num_agents, 
                                   max_available_bandwidth)
        
        return effective_throughput, network_efficiency, theoretical_throughput, real_world_efficiency
    
    def calculate_dms_throughput(self, instance_type, database_size_gb, database_types, 
                               migration_type, network_pattern, network_bw_mbps):
        """Calculate DMS throughput for database migration"""
        
        base_throughput = self.dms_performance[instance_type]["throughput_mbps"]
        
        # Database type factors
        db_factors = {
            "oracle": 0.85,
            "sql server": 0.90,
            "mysql": 0.95,
            "postgresql": 0.95,
            "mongodb": 0.80,
            "cassandra": 0.75
        }
        
        if database_types:
            avg_db_factor = np.mean([db_factors.get(db.lower(), 0.85) for db in database_types])
        else:
            avg_db_factor = 0.85
        
        # Migration type impact
        migration_factors = {
            "full_load": 1.0,
            "full_load_and_cdc": 0.8,
            "cdc_only": 0.6
        }
        migration_factor = migration_factors.get(migration_type, 0.8)
        
        # Network pattern efficiency
        pattern_efficiency = self.network_patterns.get(network_pattern, {}).get("efficiency", 0.85)
        
        # Database size impact
        size_factor = min(1.0, 0.5 + (database_size_gb / 10000))
        
        # Calculate effective throughput
        effective_throughput = (base_throughput * avg_db_factor * migration_factor * 
                              pattern_efficiency * size_factor)
        
        final_throughput = min(effective_throughput, network_bw_mbps * 0.75)
        
        # Calculate migration phases
        if migration_type == "full_load_and_cdc":
            full_load_time_hours = (database_size_gb * 8 * 1000) / (final_throughput * 3600)
            cdc_lag_minutes = max(1, database_size_gb / 1000)
        else:
            full_load_time_hours = (database_size_gb * 8 * 1000) / (final_throughput * 3600)
            cdc_lag_minutes = 0
        
        return {
            "throughput_mbps": final_throughput,
            "efficiency": final_throughput / network_bw_mbps,
            "full_load_time_hours": full_load_time_hours,
            "cdc_lag_minutes": cdc_lag_minutes,
            "database_compatibility": avg_db_factor,
            "network_efficiency": pattern_efficiency
        }
    
    def calculate_snowball_timeline(self, data_size_gb, device_type, num_devices, shipping_location):
        """Calculate Snowball migration timeline"""
        
        device_specs = self.snowball_specs[device_type]
        device_capacity_gb = device_specs["capacity_tb"] * 1024
        
        devices_needed = max(num_devices, int(np.ceil(data_size_gb / device_capacity_gb)))
        
        shipping_multipliers = {
            "domestic": 1.0,
            "international": 2.0,
            "remote": 3.0
        }
        base_shipping_days = device_specs["shipping_days"]
        shipping_days = base_shipping_days * shipping_multipliers.get(shipping_location, 1.0)
        
        transfer_rate_mbps = device_specs["transfer_rate_gbps"] * 1000
        loading_time_hours = (data_size_gb * 8) / (transfer_rate_mbps * 3600)
        loading_time_days = loading_time_hours / 24
        
        total_timeline_days = (shipping_days * 2) + loading_time_days + 2
        
        pricing = self.pricing_manager.get_snowball_pricing(device_type)
        device_cost = pricing["device_fee"] * devices_needed
        extra_days = max(0, total_timeline_days - pricing["days_included"])
        extra_day_cost = extra_days * 15 * devices_needed
        total_cost = device_cost + extra_day_cost
        
        return {
            "devices_needed": devices_needed,
            "loading_time_days": loading_time_days,
            "shipping_days": shipping_days,
            "total_timeline_days": total_timeline_days,
            "total_cost": total_cost,
            "throughput_equivalent_mbps": (data_size_gb * 8 * 1000) / (total_timeline_days * 24 * 3600),
            "device_utilization": (data_size_gb / devices_needed) / device_capacity_gb
        }
    
    def assess_compliance_requirements(self, frameworks, data_classification, data_residency):
        """Original compliance assessment"""
        requirements = set()
        risks = []
        
        for framework in frameworks:
            if framework in self.compliance_requirements:
                reqs = self.compliance_requirements[framework]
                requirements.update(reqs.keys())
                
                if framework == "GDPR" and data_residency == "No restrictions":
                    risks.append("GDPR requires data residency controls")
                
                if framework in ["HIPAA", "PCI-DSS"] and data_classification == "Public":
                    risks.append(f"{framework} incompatible with Public data classification")
        
        return list(requirements), risks
    
    def calculate_business_impact(self, transfer_days, data_types):
        """Original business impact calculation"""
        impact_weights = {
            "Customer Data": 0.9,
            "Financial Records": 0.95,
            "Employee Data": 0.7,
            "Intellectual Property": 0.85,
            "System Logs": 0.3,
            "Application Data": 0.8,
            "Database Backups": 0.6,
            "Media Files": 0.4,
            "Documents": 0.5
        }
        
        if not data_types:
            return {"score": 0.5, "level": "Medium", "recommendation": "Standard migration approach"}
        
        avg_impact = sum(impact_weights.get(dt, 0.5) for dt in data_types) / len(data_types)
        
        if avg_impact >= 0.8:
            level = "Critical"
            recommendation = "Phased migration with extensive testing"
        elif avg_impact >= 0.6:
            level = "High"
            recommendation = "Careful planning with pilot phase"
        elif avg_impact >= 0.4:
            level = "Medium"
            recommendation = "Standard migration approach"
        else:
            level = "Low"
            recommendation = "Direct migration acceptable"
        
        return {"score": avg_impact, "level": level, "recommendation": recommendation}
    
    def get_optimal_networking_architecture(self, source_location, target_region, data_size_gb, 
                                      dx_bandwidth_mbps, database_types, data_types, config=None):
        """Enhanced networking architecture with multi-service support"""
        
        data_size_gb = float(data_size_gb) if data_size_gb else 1000
        dx_bandwidth_mbps = float(dx_bandwidth_mbps) if dx_bandwidth_mbps else 1000
        data_size_tb = data_size_gb / 1024
        
        estimated_latency = self.geographic_latency.get(source_location, {}).get(target_region, 50)
        estimated_latency = float(estimated_latency)
        
        has_databases = len(database_types) > 0
        has_large_files = any("Large" in dt or "Media" in dt for dt in data_types)
        
        recommendations = {
            "primary_method": "",
            "secondary_method": "",
            "networking_option": "",
            "db_migration_tool": "",
            "rationale": "",
            "estimated_performance": {},
            "cost_efficiency": "",
            "risk_level": "",
            "ai_analysis": "",
            "service_recommendations": {}
        }
        
        # Network architecture decision
        if dx_bandwidth_mbps >= 1000 and estimated_latency < 50:
            recommendations["networking_option"] = "Direct Connect (Primary)"
            network_score = 9
        elif dx_bandwidth_mbps >= 500:
            recommendations["networking_option"] = "Direct Connect with Internet Backup"
            network_score = 7
        else:
            recommendations["networking_option"] = "Internet with VPN"
            network_score = 5
        
# REPLACE DMS calculation with more realistic version:

        if has_databases and data_size_tb <= 50:
            # DMS calculation with realistic overhead
            dms_throughput = min(1000, dx_bandwidth_mbps * 0.7)
            
            # DMS is typically slower due to database complexity
            if config and config.get('database_size_gb'):
                db_size_gb = config['database_size_gb']
            else:
                db_size_gb = data_size_gb * 0.3  # Assume 30% is database data
            
            # Database migrations have different overhead than file transfers
            db_overhead_factor = 2.0  # Databases take longer due to consistency requirements
            base_transfer_hours = (db_size_gb * 8 * 1000) / (dms_throughput * 3600)
            dms_days = max(0.25, (base_transfer_hours / 24) * db_overhead_factor)  # Minimum 6 hours
            
            recommendations["service_recommendations"]["dms"] = {
                "suitability": "High",
                "throughput_mbps": dms_throughput,
                "estimated_days": dms_days,
                "pros": ["Minimal downtime", "CDC support", "Database optimized"],
                "cons": ["Database only", "Complex setup"]
            }
        
        # REPLACE with more realistic Snowball calculation:

        # Snowball calculation - based on physical logistics not network speed
        if data_size_tb > 50:
            # Snowball timeline is mostly shipping + loading time
            shipping_days = 6  # Round trip shipping
            loading_time_days = max(1, data_size_tb / 8)  # ~8TB per day loading rate
            processing_days = 2  # AWS processing time
            total_snowball_days = shipping_days + loading_time_days + processing_days
            
            # Equivalent throughput for comparison (not actual network throughput)
            equivalent_mbps = (data_size_gb * 8 * 1000) / (total_snowball_days * 24 * 3600)
            
            recommendations["service_recommendations"]["snowball"] = {
                "suitability": "High" if data_size_tb > 100 else "Medium", 
                "throughput_mbps": equivalent_mbps,
                "estimated_days": total_snowball_days,
                "pros": ["No bandwidth dependency", "Secure", "Cost-effective"],
                "cons": ["Longer timeline", "Physical logistics"]
            }
        
            # REPLACE THE ENTIRE BLOCK ABOVE WITH THIS FIXED VERSION:

        # DataSync always available (FIXED CALCULATION)
        datasync_throughput = min(dx_bandwidth_mbps * 0.8, 2000)
        
        # Fix timeline calculation with proper units and realistic overhead
        if data_size_gb > 0 and datasync_throughput > 0:
            # Convert GB to bits: GB * 8 * 1,000,000,000 (bits per GB)
            data_size_bits = data_size_gb * 8 * 1_000_000_000
            # Convert Mbps to bits per second: Mbps * 1,000,000 
            throughput_bits_per_second = datasync_throughput * 1_000_000
            
            # Calculate base transfer time in seconds
            transfer_seconds = data_size_bits / throughput_bits_per_second
            # Convert to days
            base_days = transfer_seconds / (24 * 3600)
            
            # Add realistic overhead factors for DataSync operations
            setup_overhead = 0.1    # 10% for setup and initialization
            retry_overhead = 0.2    # 20% for retries and error handling  
            validation_overhead = 0.1  # 10% for validation and verification
            
            total_overhead = 1 + setup_overhead + retry_overhead + validation_overhead
            datasync_days = base_days * total_overhead
            
            # Set realistic minimums based on data size
            if data_size_gb < 100:  # Less than 100GB
                datasync_days = max(0.125, datasync_days)  # Minimum 3 hours
            elif data_size_gb < 1000:  # Less than 1TB
                datasync_days = max(0.25, datasync_days)   # Minimum 6 hours  
            else:  # 1TB or more
                datasync_days = max(0.5, datasync_days)    # Minimum 12 hours
        else:
            datasync_days = 1.0  # Default fallback
        
        recommendations["service_recommendations"]["datasync"] = {
            "suitability": "High" if not has_databases else "Medium",
            "throughput_mbps": datasync_throughput,
            "estimated_days": datasync_days,
            "pros": ["File optimized", "Incremental sync", "Real-time monitoring"],
            "cons": ["Network dependent", "File-based only"]
        }
        
        # Select primary method
        if has_databases and data_size_tb <= 50:
            recommendations["primary_method"] = "DMS"
        elif data_size_tb > 100 and dx_bandwidth_mbps < 1000:
            recommendations["primary_method"] = "Snowball Edge"
        else:
            recommendations["primary_method"] = "DataSync"
        
        recommendations["secondary_method"] = "S3 Transfer Acceleration"
        
        # Performance estimate for primary method
        primary_service = recommendations["service_recommendations"].get(
            recommendations["primary_method"].lower().replace(" ", "_").replace("edge", ""), 
            recommendations["service_recommendations"]["datasync"]
        )
        
        recommendations["estimated_performance"] = {
            "throughput_mbps": primary_service["throughput_mbps"],
            "estimated_days": primary_service["estimated_days"],
            "network_efficiency": network_score / 10,
            "agents_used": config.get('num_datasync_agents', 1) if config else 1,
            "instance_type": config.get('datasync_instance_type', 'm5.large') if config else 'm5.large'
        }
        
        # Generate rationale
        recommendations["rationale"] = self._generate_ai_rationale(
            source_location, target_region, data_size_tb, dx_bandwidth_mbps, 
            has_databases, has_large_files, estimated_latency, network_score
        )
        
        # Cost and risk assessment
        if data_size_tb > 100 and dx_bandwidth_mbps < 1000:
            recommendations["cost_efficiency"] = "High (Physical transfer)"
            recommendations["risk_level"] = "Medium"
        elif dx_bandwidth_mbps >= 1000:
            recommendations["cost_efficiency"] = "Medium (Network transfer)"
            recommendations["risk_level"] = "Low"
        else:
            recommendations["cost_efficiency"] = "Medium"
            recommendations["risk_level"] = "Medium"
        
        return recommendations
    
    def _generate_ai_rationale(self, source, target, data_size_tb, bandwidth, has_db, has_large_files, latency, network_score):
        """Generate AI rationale"""
        rationale_parts = []
        
        if latency < 30:
            rationale_parts.append(f"Excellent geographic proximity between {source} and {target} (≈{latency}ms latency)")
        elif latency < 80:
            rationale_parts.append(f"Good connectivity between {source} and {target} (≈{latency}ms latency)")
        else:
            rationale_parts.append(f"Significant distance between {source} and {target} (≈{latency}ms latency)")
        
        if bandwidth >= 10000:
            rationale_parts.append("High-bandwidth Direct Connect enables optimal network transfer performance")
        elif bandwidth >= 1000:
            rationale_parts.append("Adequate Direct Connect bandwidth supports efficient network-based migration")
        else:
            rationale_parts.append("Limited bandwidth suggests physical transfer methods for large datasets")
        
        if data_size_tb > 100:
            rationale_parts.append(f"Large dataset ({data_size_tb:.1f}TB) requires high-throughput migration strategy")
        
        if has_db:
            rationale_parts.append("Database workloads require specialized migration tools with minimal downtime capabilities")
        
        if has_large_files:
            rationale_parts.append("Large file presence optimizes for high-throughput, parallel transfer methods")
        
        if network_score >= 8:
            rationale_parts.append("Network conditions are optimal for direct cloud migration")
        elif network_score >= 6:
            rationale_parts.append("Network conditions support cloud migration with some optimization needed")
        else:
            rationale_parts.append("Network limitations suggest hybrid or physical transfer approaches")
        
        return ". ".join(rationale_parts) + "."
    
    def calculate_enterprise_costs(self, data_size_gb, transfer_days, instance_type, num_agents, 
                                compliance_frameworks, s3_storage_class, region=None, dx_bandwidth_mbps=1000):
        """Enhanced cost calculation with multi-service support"""
        
        with st.spinner("🔄 Fetching real-time AWS pricing..."):
            pricing = self.pricing_manager.get_comprehensive_pricing(
                instance_type=instance_type,
                storage_class=s3_storage_class,
                region=region,
                bandwidth_mbps=dx_bandwidth_mbps
            )
        
        # DataSync costs
        instance_cost_hour = pricing['ec2']
        datasync_compute_cost = instance_cost_hour * num_agents * 24 * transfer_days
        
        # Data transfer costs
        transfer_rate_per_gb = pricing['transfer']
        data_transfer_cost = data_size_gb * transfer_rate_per_gb
        
        # S3 storage costs
        s3_rate_per_gb = pricing['s3']
        s3_storage_cost = data_size_gb * s3_rate_per_gb
        
        # Direct Connect costs
        dx_hourly_cost = pricing['dx']
        dx_cost = dx_hourly_cost * 24 * transfer_days
        
        # Additional costs
        compliance_cost = len(compliance_frameworks) * 500
        monitoring_cost = 200 * transfer_days
        datasync_service_cost = data_size_gb * 0.0125
        cloudwatch_cost = num_agents * 50 * transfer_days
        
        total_cost = (datasync_compute_cost + data_transfer_cost + s3_storage_cost + 
                    dx_cost + compliance_cost + monitoring_cost + datasync_service_cost + 
                    cloudwatch_cost)
        
        return {
            "compute": datasync_compute_cost,
            "transfer": data_transfer_cost,
            "storage": s3_storage_cost,
            "direct_connect": dx_cost,
            "datasync_service": datasync_service_cost,
            "compliance": compliance_cost,
            "monitoring": monitoring_cost,
            "cloudwatch": cloudwatch_cost,
            "total": total_cost,
            "pricing_source": "AWS API" if self.pricing_manager.pricing_client else "Fallback",
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "cost_breakdown_detailed": {
                "instance_hourly_rate": instance_cost_hour,
                "transfer_rate_per_gb": transfer_rate_per_gb,
                "s3_rate_per_gb": s3_rate_per_gb,
                "dx_hourly_rate": dx_hourly_cost
            }
        }
    
    def get_real_ai_analysis(self, config, api_key, model="claude-sonnet-4-20250514"):
        """Real Claude AI analysis using Anthropic API"""
        if not ANTHROPIC_AVAILABLE or not api_key:
            return None
        
        try:
            client = anthropic.Anthropic(api_key=api_key)
            
            context = f"""
            You are an expert AWS migration architect. Analyze this migration scenario:
            
            Project: {config.get('project_name', 'N/A')}
            Data Size: {config.get('data_size_gb', 0)} GB
            Source: {config.get('source_location', 'N/A')}
            Target: {config.get('target_aws_region', 'N/A')}
            Network: {config.get('dx_bandwidth_mbps', 0)} Mbps
            Services: {', '.join(config.get('selected_services', []))}
            Databases: {', '.join(config.get('database_types', []))}
            Data Types: {', '.join(config.get('data_types', []))}
            Compliance: {', '.join(config.get('compliance_frameworks', []))}
            
            Provide specific recommendations for:
            1. Best service combination (DataSync/DMS/Snowball)
            2. Network architecture approach
            3. Performance optimization strategies
            4. Risk mitigation approaches
            5. Cost optimization suggestions
            
            Be concise but specific. Focus on AWS best practices.
            """
            
            response = client.messages.create(
                model=model,
                max_tokens=1000,
                temperature=0.3,
                messages=[{"role": "user", "content": context}]
            )
            
            return response.content[0].text if response.content else None
            
        except Exception as e:
            st.error(f"Claude AI API Error: {str(e)}")
            return None

class PDFReportGenerator:
    """PDF report generation with enhanced multi-service support"""
    
    def __init__(self):
        if not PDF_AVAILABLE:
            return
            
        self.styles = getSampleStyleSheet()
        self.title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            textColor=colors.darkblue,
            alignment=1
        )
        self.heading_style = ParagraphStyle(
            'CustomHeading',
            parent=self.styles['Heading2'],
            fontSize=16,
            spaceAfter=12,
            textColor=colors.darkblue,
            leftIndent=0
        )
    
    def generate_comprehensive_report(self, config, metrics, recommendations):
        """Generate comprehensive multi-service migration report"""
        if not PDF_AVAILABLE:
            return None
            
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=18)
        
        story = []
        
        # Title Page
        story.append(Paragraph("Enterprise AWS Migration Strategy Report", self.title_style))
        story.append(Paragraph("Multi-Service Analysis & Strategic Recommendation", self.styles['Heading2']))
        story.append(Spacer(1, 30))
        
        # Executive Summary
        selected_services = config.get('selected_services', [])
        exec_summary = f"""
        <b>Project:</b> {config['project_name']}<br/>
        <b>Data Volume:</b> {config['data_size_gb']:,} GB<br/>
        <b>Services Analyzed:</b> {', '.join([s.upper() for s in selected_services])}<br/>
        <b>Primary Recommendation:</b> {recommendations.get('primary_method', 'N/A')}<br/>
        <b>Network Pattern:</b> {recommendations.get('networking_option', 'N/A')}<br/>
        <b>Risk Level:</b> {recommendations.get('risk_level', 'Medium')}
        """
        story.append(Paragraph(exec_summary, self.styles['Normal']))
        story.append(Spacer(1, 20))
        
        # Service Analysis Section
        story.append(Paragraph("Service Analysis Summary", self.heading_style))
        
        service_data = []
        if 'service_recommendations' in recommendations:
            for service, rec in recommendations['service_recommendations'].items():
                service_data.append([
                    service.upper(),
                    rec.get('suitability', 'N/A'),
                    f"{rec.get('throughput_mbps', 0):.0f} Mbps",
                    f"{rec.get('estimated_days', 0):.1f} days"
                ])
        
        if service_data:
            service_table = Table([['Service', 'Suitability', 'Throughput', 'Timeline']] + service_data,
                                colWidths=[1.5*inch, 1*inch, 1.5*inch, 1.5*inch])
            service_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            story.append(service_table)
        
        story.append(Spacer(1, 20))
        
        # AI Recommendations
        story.append(Paragraph("AI Strategic Analysis", self.heading_style))
        ai_text = f"<b>Rationale:</b> {recommendations.get('rationale', 'No analysis available')}"
        story.append(Paragraph(ai_text, self.styles['Normal']))
        
        # Build PDF
        doc.build(story)
        buffer.seek(0)
        return buffer

class MigrationPlatform:
    """Complete migration platform with all original features plus multi-service support"""
    
    def __init__(self):
        self.calculator = EnhancedMigrationCalculator()
        self.pdf_generator = PDFReportGenerator() if PDF_AVAILABLE else None
        self.initialize_session_state()
        self.setup_custom_css()
        self.last_update_time = datetime.now()
        self.auto_refresh_interval = 30
    
    def initialize_session_state(self):
        """Initialize all session state variables"""
        if 'migration_projects' not in st.session_state:
            st.session_state.migration_projects = {}
        if 'user_profile' not in st.session_state:
            st.session_state.user_profile = {
                'role': 'Network Architect',
                'organization': 'Enterprise Corp',
                'security_clearance': 'Standard'
            }
        if 'audit_log' not in st.session_state:
            st.session_state.audit_log = []
        if 'active_tab' not in st.session_state:
            st.session_state.active_tab = "dashboard"
        if 'selected_services' not in st.session_state:
            st.session_state.selected_services = ["datasync"]
        if 'last_config_hash' not in st.session_state:
            st.session_state.last_config_hash = None
        if 'config_change_count' not in st.session_state:
            st.session_state.config_change_count = 0
    
    def setup_custom_css(self):
        """Enhanced CSS styling"""
        st.markdown("""
        <style>
            .main-header {
                background: linear-gradient(135deg, #FF9900 0%, #232F3E 100%);
                padding: 2rem;
                border-radius: 15px;
                color: white;
                text-align: center;
                margin-bottom: 2rem;
                box-shadow: 0 8px 32px rgba(0,0,0,0.1);
            }
            
            .service-card {
                background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
                padding: 1.5rem;
                border-radius: 12px;
                border-left: 5px solid #007bff;
                margin: 1rem 0;
                transition: all 0.3s ease;
                box-shadow: 0 2px 12px rgba(0,0,0,0.08);
            }
            
            .service-card:hover {
                transform: translateY(-3px);
                box-shadow: 0 6px 20px rgba(0,0,0,0.15);
            }
            
            .tab-container {
                background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
                padding: 1.5rem;
                border-radius: 12px;
                margin-bottom: 2rem;
                box-shadow: 0 4px 16px rgba(0,0,0,0.1);
                border: 1px solid #dee2e6;
            }
            
            .section-header {
                background: linear-gradient(135deg, #007bff 0%, #0056b3 100%);
                color: white;
                padding: 1rem 1.5rem;
                border-radius: 8px;
                margin: 1.5rem 0 1rem 0;
                font-size: 1.2rem;
                font-weight: bold;
                box-shadow: 0 2px 8px rgba(0,123,255,0.3);
            }
            
            .metric-card {
                background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
                padding: 1.5rem;
                border-radius: 12px;
                border-left: 5px solid #FF9900;
                margin: 0.75rem 0;
                transition: all 0.3s ease;
                box-shadow: 0 2px 12px rgba(0,0,0,0.08);
                border: 1px solid #e9ecef;
            }
            
            .metric-card:hover {
                background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
                transform: translateY(-3px);
                box-shadow: 0 6px 20px rgba(0,0,0,0.15);
            }
            
            .recommendation-box {
                background: linear-gradient(135deg, #e8f4fd 0%, #f0f8ff 100%);
                padding: 1.5rem;
                border-radius: 12px;
                border-left: 5px solid #007bff;
                margin: 1rem 0;
                box-shadow: 0 3px 15px rgba(0,123,255,0.1);
                border: 1px solid #b8daff;
            }
            
            .ai-insight {
                background: linear-gradient(135deg, #f0f8ff 0%, #e6f3ff 100%);
                padding: 1.25rem;
                border-radius: 10px;
                border-left: 4px solid #007bff;
                margin: 1rem 0;
                font-style: italic;
                box-shadow: 0 2px 10px rgba(0,123,255,0.1);
                border: 1px solid #cce7ff;
            }
            
            .real-time-indicator {
                display: inline-block;
                width: 10px;
                height: 10px;
                background: linear-gradient(135deg, #28a745 0%, #20c997 100%);
                border-radius: 50%;
                animation: pulse 2s infinite;
                margin-right: 8px;
                box-shadow: 0 0 8px rgba(40,167,69,0.5);
            }
            
            @keyframes pulse {
                0% { opacity: 1; transform: scale(1); }
                50% { opacity: 0.7; transform: scale(1.1); }
                100% { opacity: 1; transform: scale(1); }
            }
        </style>
        """, unsafe_allow_html=True)
    
    def detect_configuration_changes(self, config):
        """Detect configuration changes"""
        import hashlib
        
        config_str = json.dumps(config, sort_keys=True, default=str)
        current_hash = hashlib.md5(config_str.encode()).hexdigest()
        
        if st.session_state.last_config_hash != current_hash:
            if st.session_state.last_config_hash is not None:
                st.session_state.config_change_count += 1
                self.log_audit_event("CONFIG_CHANGED", f"Configuration updated - Change #{st.session_state.config_change_count}")
            
            st.session_state.last_config_hash = current_hash
            return True
        return False
    
    def log_audit_event(self, event_type, details):
        """Log audit events"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "details": details,
            "user": st.session_state.user_profile["role"]
        }
        st.session_state.audit_log.append(event)
    
    def render_header(self):
        """Render the enhanced main header"""
        st.markdown("""
        <div class="main-header">
            <h1>🏢 Complete Enterprise AWS Migration Platform</h1>
            <p style="font-size: 1.1rem; margin-top: 0.5rem;">Multi-Service Support • DataSync • DMS • Snowball • Network Patterns • AI-Powered • Enterprise-Grade</p>
            <p style="font-size: 0.9rem; margin-top: 0.5rem; opacity: 0.9;">Real-time Pricing • Security-First • Compliance-Ready • Professional Reporting</p>
        </div>
        """, unsafe_allow_html=True)
    
    def render_navigation(self):
        """Render enhanced navigation"""
        st.markdown('<div class="tab-container">', unsafe_allow_html=True)
        
        col1, col2, col3, col4, col5, col6, col7 = st.columns([2, 2, 2, 2, 2, 2, 2])
        
        with col1:
            if st.button("🏠 Dashboard", key="nav_dashboard"):
                st.session_state.active_tab = "dashboard"
        with col2:
            if st.button("🔧 Multi-Service", key="nav_multiservice"):
                st.session_state.active_tab = "multiservice"
        with col3:
            if st.button("🌐 Network Analysis", key="nav_network"):
                st.session_state.active_tab = "network"
        with col4:
            if st.button("⚡ Performance", key="nav_performance"):
                st.session_state.active_tab = "performance"
        with col5:
            if st.button("🔒 Security", key="nav_security"):
                st.session_state.active_tab = "security"
        with col6:
            if st.button("📈 Analytics", key="nav_analytics"):
                st.session_state.active_tab = "analytics"
        with col7:
            if st.button("🎯 Conclusion", key="nav_conclusion"):
                st.session_state.active_tab = "conclusion"
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    def render_service_selection(self):
        """Enhanced service selection"""
        st.sidebar.subheader("🔧 Migration Services")
        st.sidebar.write("Select services to analyze:")
        
        services = {
            "datasync": "📁 AWS DataSync (Files & Objects)",
            "dms": "🗄️ Database Migration Service",
            "snowball": "📦 Snowball Family (Physical Transfer)",
            "network_patterns": "🌐 Network Pattern Analysis"
        }
        
        selected_services = []
        for service_key, service_name in services.items():
            if st.sidebar.checkbox(service_name, 
                                 value=service_key in st.session_state.selected_services,
                                 key=f"service_{service_key}"):
                selected_services.append(service_key)
        
        st.session_state.selected_services = selected_services
        return selected_services
    
    def render_sidebar_controls(self):
        """Enhanced sidebar controls with all original features"""
        st.sidebar.header("🏢 Enterprise Controls")
        
        # Get AWS configuration status
        aws_config = self.render_aws_credentials_section()
        
        # Service selection
        selected_services = self.render_service_selection()
        
        # Project management section
        st.sidebar.subheader("📁 Project Management")
        project_name = st.sidebar.text_input("Project Name", value="Multi-Service-Migration-2025")
        business_unit = st.sidebar.selectbox("Business Unit", 
            ["Corporate IT", "Finance", "HR", "Operations", "R&D", "Sales & Marketing"])
        project_priority = st.sidebar.selectbox("Project Priority", 
            ["Critical", "High", "Medium", "Low"])
        migration_wave = st.sidebar.selectbox("Migration Wave", 
            ["Wave 1 (Pilot)", "Wave 2 (Core Systems)", "Wave 3 (Secondary)", "Wave 4 (Archive)"])
        
        # Security and compliance section
        st.sidebar.subheader("🔒 Security & Compliance")
        data_classification = st.sidebar.selectbox("Data Classification", 
            ["Public", "Internal", "Confidential", "Restricted", "Top Secret"])
        compliance_frameworks = st.sidebar.multiselect("Compliance Requirements", 
            ["SOX", "GDPR", "HIPAA", "PCI-DSS", "SOC2", "ISO27001", "FedRAMP", "FISMA"])
        encryption_in_transit = st.sidebar.checkbox("Encryption in Transit", value=True)
        encryption_at_rest = st.sidebar.checkbox("Encryption at Rest", value=True)
        data_residency = st.sidebar.selectbox("Data Residency Requirements", 
            ["No restrictions", "US only", "EU only", "Specific region", "On-premises only"])
        
        # Enterprise parameters
        st.sidebar.subheader("🎯 Enterprise Parameters")
        sla_requirements = st.sidebar.selectbox("SLA Requirements", 
            ["99.9% availability", "99.95% availability", "99.99% availability", "99.999% availability"])
        rto_hours = st.sidebar.number_input("Recovery Time Objective (hours)", min_value=1, max_value=168, value=4)
        rpo_hours = st.sidebar.number_input("Recovery Point Objective (hours)", min_value=0, max_value=24, value=1)
        max_transfer_days = st.sidebar.number_input("Maximum Transfer Days", min_value=1, max_value=90, value=30)
        budget_allocated = st.sidebar.number_input("Allocated Budget ($)", min_value=1000, max_value=10000000, value=100000, step=1000)
        approval_required = st.sidebar.checkbox("Executive Approval Required", value=True)
        
        # Data characteristics
        st.sidebar.subheader("📊 Data Profile")
        data_size_gb = st.sidebar.number_input("Total Data Size (GB)", min_value=1, max_value=1000000, value=10000, step=100)
        data_types = st.sidebar.multiselect("Data Types", 
            ["Customer Data", "Financial Records", "Employee Data", "Intellectual Property", 
             "System Logs", "Application Data", "Database Backups", "Media Files", "Documents"])
        database_types = st.sidebar.multiselect("Database Systems", 
            ["Oracle", "SQL Server", "MySQL", "PostgreSQL", "MongoDB", "Cassandra", "Redis", "Elasticsearch"])
        avg_file_size = st.sidebar.selectbox("Average File Size",
            ["< 1MB (Many small files)", "1-10MB (Small files)", "10-100MB (Medium files)", 
             "100MB-1GB (Large files)", "> 1GB (Very large files)"])
        data_growth_rate = st.sidebar.slider("Annual Data Growth Rate (%)", min_value=0, max_value=100, value=20)
        data_volatility = st.sidebar.selectbox("Data Change Frequency", 
            ["Static (rarely changes)", "Low (daily changes)", "Medium (hourly changes)", "High (real-time)"])
        
        # Network infrastructure
        st.sidebar.subheader("🌐 Network Configuration")
        network_topology = st.sidebar.selectbox("Network Topology", 
            ["Single DX", "Redundant DX", "Hybrid (DX + VPN)", "Multi-region", "SD-WAN"])
        network_pattern = st.sidebar.selectbox("Network Pattern",
            ["direct_connect_dedicated", "direct_connect_hosted", "site_to_site_vpn", "transit_gateway"])
        dx_bandwidth_mbps = st.sidebar.number_input("Primary DX Bandwidth (Mbps)", min_value=50, max_value=100000, value=10000, step=100)
        dx_redundant = st.sidebar.checkbox("Redundant DX Connection", value=True)
        if dx_redundant:
            dx_secondary_mbps = st.sidebar.number_input("Secondary DX Bandwidth (Mbps)", min_value=50, max_value=100000, value=10000, step=100)
        else:
            dx_secondary_mbps = 0
        
        network_latency = st.sidebar.slider("Network Latency to AWS (ms)", min_value=1, max_value=500, value=25)
        network_jitter = st.sidebar.slider("Network Jitter (ms)", min_value=0, max_value=50, value=5)
        packet_loss = st.sidebar.slider("Packet Loss (%)", min_value=0.0, max_value=5.0, value=0.1, step=0.1)
        qos_enabled = st.sidebar.checkbox("QoS Enabled", value=True)
        dedicated_bandwidth = st.sidebar.slider("Dedicated Migration Bandwidth (%)", min_value=10, max_value=90, value=60)
        business_hours_restriction = st.sidebar.checkbox("Restrict to Off-Business Hours", value=True)
        
        # Service-specific configurations
        if "datasync" in selected_services:
            st.sidebar.subheader("📁 DataSync Configuration")
            if hasattr(st.session_state, 'auto_apply_agents'):
                num_datasync_agents = st.session_state.auto_apply_agents
                del st.session_state.auto_apply_agents
                st.sidebar.success(f"✅ Applied AI recommendation: {num_datasync_agents} agents")
            else:
                num_datasync_agents = st.sidebar.number_input("DataSync Agents", min_value=1, max_value=50, value=5)
            
            if hasattr(st.session_state, 'auto_apply_instance'):
                datasync_instance_type = st.session_state.auto_apply_instance
                del st.session_state.auto_apply_instance
                st.sidebar.success(f"✅ Applied AI recommendation: {datasync_instance_type}")
            else:
                datasync_instance_type = st.sidebar.selectbox("DataSync Instance Type",
                    ["m5.large", "m5.xlarge", "m5.2xlarge", "m5.4xlarge", "m5.8xlarge", 
                     "c5.2xlarge", "c5.4xlarge", "c5.9xlarge", "r5.2xlarge", "r5.4xlarge"])
        else:
            num_datasync_agents = 1
            datasync_instance_type = "m5.large"
        
        if "dms" in selected_services:
            st.sidebar.subheader("🗄️ DMS Configuration")
            dms_instance_type = st.sidebar.selectbox("DMS Instance Type",
                ["dms.t3.large", "dms.c5.large", "dms.c5.xlarge", "dms.c5.2xlarge", "dms.c5.4xlarge",
                 "dms.r5.large", "dms.r5.xlarge", "dms.r5.2xlarge", "dms.r5.4xlarge"])
            database_size_gb = st.sidebar.number_input("Total Database Size (GB)", min_value=1, max_value=100000, value=5000)
            migration_type = st.sidebar.selectbox("Migration Type",
                ["full_load", "full_load_and_cdc", "cdc_only"])
        else:
            dms_instance_type = "dms.c5.large"
            database_size_gb = 1000
            migration_type = "full_load_and_cdc"
        
        if "snowball" in selected_services:
            st.sidebar.subheader("📦 Snowball Configuration")
            snowball_device_type = st.sidebar.selectbox("Device Type",
                ["snowcone", "snowball_edge_storage", "snowball_edge_compute"])
            num_snowball_devices = st.sidebar.number_input("Number of Devices", min_value=1, max_value=20, value=1)
            shipping_location = st.sidebar.selectbox("Shipping Location",
                ["domestic", "international", "remote"])
        else:
            snowball_device_type = "snowball_edge_storage"
            num_snowball_devices = 1
            shipping_location = "domestic"
        
        # Real-world performance modeling
        st.sidebar.subheader("📊 Performance Modeling")
        real_world_mode = st.sidebar.checkbox("Real-world Performance Mode", value=True, 
            help="Include real-world factors like storage I/O, service overhead, and API limits")
        
        # Network optimization
        st.sidebar.subheader("🌐 Network Optimization")
        tcp_window_size = st.sidebar.selectbox("TCP Window Size", 
            ["Default", "64KB", "128KB", "256KB", "512KB", "1MB", "2MB"])
        mtu_size = st.sidebar.selectbox("MTU Size", 
            ["1500 (Standard)", "9000 (Jumbo Frames)", "Custom"])
        if mtu_size == "Custom":
            custom_mtu = st.sidebar.number_input("Custom MTU", min_value=1280, max_value=9216, value=1500)
        
        network_congestion_control = st.sidebar.selectbox("Congestion Control Algorithm",
            ["Cubic (Default)", "BBR", "Reno", "Vegas"])
        wan_optimization = st.sidebar.checkbox("WAN Optimization", value=False)
        parallel_streams = st.sidebar.slider("Parallel Streams per Agent", min_value=1, max_value=100, value=20)
        use_transfer_acceleration = st.sidebar.checkbox("S3 Transfer Acceleration", value=True)
        
        # Storage configuration
        st.sidebar.subheader("💾 Storage Strategy")
        s3_storage_class = st.sidebar.selectbox("Primary S3 Storage Class",
            ["Standard", "Standard-IA", "One Zone-IA", "Glacier Instant Retrieval", 
             "Glacier Flexible Retrieval", "Glacier Deep Archive"])
        enable_versioning = st.sidebar.checkbox("Enable S3 Versioning", value=True)
        enable_lifecycle = st.sidebar.checkbox("Lifecycle Policies", value=True)
        cross_region_replication = st.sidebar.checkbox("Cross-Region Replication", value=False)
        
        # Geographic configuration
        st.sidebar.subheader("🗺️ Geographic Settings")
        source_location = st.sidebar.selectbox("Source Data Center Location",
            ["San Jose, CA", "San Antonio, TX", "New York, NY", "Chicago, IL", "Dallas, TX", 
             "Los Angeles, CA", "Atlanta, GA", "London, UK", "Frankfurt, DE", "Tokyo, JP", "Sydney, AU", "Other"])
        target_aws_region = st.sidebar.selectbox("Target AWS Region",
            ["us-east-1 (N. Virginia)", "us-east-2 (Ohio)", "us-west-1 (N. California)", 
             "us-west-2 (Oregon)", "eu-west-1 (Ireland)", "eu-central-1 (Frankfurt)",
             "ap-southeast-1 (Singapore)", "ap-northeast-1 (Tokyo)"])
        
        # AI Configuration
        st.sidebar.subheader("🤖 AI Configuration")
        enable_real_ai = st.sidebar.checkbox("Enable Real Claude AI API", value=False)
        
        if enable_real_ai:
            if ANTHROPIC_AVAILABLE:
                claude_api_key = st.sidebar.text_input(
                    "Claude API Key", 
                    type="password", 
                    help="Enter your Anthropic Claude API key for enhanced AI analysis"
                )
                ai_model = st.sidebar.selectbox(
                    "AI Model", 
                    ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-7-sonnet-20250219"],
                    help="Select Claude model for analysis"
                )
            else:
                st.sidebar.error("Anthropic library not installed. Run: pip install anthropic")
                claude_api_key = ""
                ai_model = "claude-sonnet-4-20250514"
        else:
            claude_api_key = ""
            ai_model = "claude-sonnet-4-20250514"
            st.sidebar.info("Using built-in AI simulation")
        
        return {
            'project_name': project_name,
            'business_unit': business_unit,
            'project_priority': project_priority,
            'migration_wave': migration_wave,
            'data_classification': data_classification,
            'compliance_frameworks': compliance_frameworks,
            'encryption_in_transit': encryption_in_transit,
            'encryption_at_rest': encryption_at_rest,
            'data_residency': data_residency,
            'sla_requirements': sla_requirements,
            'rto_hours': rto_hours,
            'rpo_hours': rpo_hours,
            'max_transfer_days': max_transfer_days,
            'budget_allocated': budget_allocated,
            'approval_required': approval_required,
            'data_size_gb': data_size_gb,
            'data_types': data_types,
            'database_types': database_types,
            'avg_file_size': avg_file_size,
            'data_growth_rate': data_growth_rate,
            'data_volatility': data_volatility,
            'network_topology': network_topology,
            'network_pattern': network_pattern,
            'dx_bandwidth_mbps': dx_bandwidth_mbps,
            'dx_redundant': dx_redundant,
            'dx_secondary_mbps': dx_secondary_mbps,
            'network_latency': network_latency,
            'network_jitter': network_jitter,
            'packet_loss': packet_loss,
            'qos_enabled': qos_enabled,
            'dedicated_bandwidth': dedicated_bandwidth,
            'business_hours_restriction': business_hours_restriction,
            'num_datasync_agents': num_datasync_agents,
            'datasync_instance_type': datasync_instance_type,
            'dms_instance_type': dms_instance_type,
            'database_size_gb': database_size_gb,
            'migration_type': migration_type,
            'snowball_device_type': snowball_device_type,
            'num_snowball_devices': num_snowball_devices,
            'shipping_location': shipping_location,
            'tcp_window_size': tcp_window_size,
            'mtu_size': mtu_size,
            'network_congestion_control': network_congestion_control,
            'wan_optimization': wan_optimization,
            'parallel_streams': parallel_streams,
            'use_transfer_acceleration': use_transfer_acceleration,
            's3_storage_class': s3_storage_class,
            'enable_versioning': enable_versioning,
            'enable_lifecycle': enable_lifecycle,
            'cross_region_replication': cross_region_replication,
            'source_location': source_location,
            'target_aws_region': target_aws_region,
            'enable_real_ai': enable_real_ai,
            'claude_api_key': claude_api_key,
            'ai_model': ai_model,
            'real_world_mode': real_world_mode,
            'selected_services': selected_services,
            'use_aws_pricing': aws_config['use_aws_pricing'],
            'aws_region': aws_config['aws_region'],
            'aws_configured': aws_config['aws_configured']
        }
    
    def render_aws_credentials_section(self):
        """AWS credentials configuration"""
        with st.sidebar:
            st.subheader("🔑 AWS Configuration")
            
            aws_configured = False
            aws_region = 'us-east-1'
        
        try:
            if hasattr(st, 'secrets') and 'aws' in st.secrets:
                aws_configured = True
                aws_region = st.secrets["aws"].get("region", "us-east-1")
                
                st.success("✅ AWS credentials configured")
                st.write(f"**Region:** {aws_region}")
                
                available_keys = list(st.secrets["aws"].keys())
                st.write(f"**Available keys:** {', '.join(available_keys)}")
                
                if st.button("🔄 Refresh AWS Connection"):
                    st.rerun()
                    
            else:
                st.warning("⚠️ AWS credentials not configured")
                st.info("Add credentials to `.streamlit/secrets.toml`")
                
        except Exception as e:
            st.error(f"Error reading AWS secrets: {str(e)}")
        
        use_aws_pricing = st.checkbox(
            "Enable Real-time AWS Pricing", 
            value=aws_configured,
            help="Use AWS Pricing API for real-time cost calculations",
            disabled=not aws_configured
        )
        
        if not aws_configured:
            with st.expander("📋 Example secrets.toml"):
                st.code("""
[aws]
access_key_id = "AKIAIOSFODNN7EXAMPLE"
secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
region = "us-east-1"
                """, language="toml")
        
        return {
            'use_aws_pricing': use_aws_pricing,
            'aws_region': aws_region,
            'aws_configured': aws_configured
        }
    
    def calculate_migration_metrics(self, config):
        """Enhanced metrics calculation with multi-service support"""
        try:
            # Basic calculations
            data_size_gb = float(config.get('data_size_gb', 1000))
            data_size_tb = max(0.1, data_size_gb / 1024)
            effective_data_gb = data_size_gb * 0.85
            
            # Network parameters
            dx_bandwidth_mbps = float(config.get('dx_bandwidth_mbps', 1000))
            network_latency = float(config.get('network_latency', 25))
            network_jitter = float(config.get('network_jitter', 5))
            packet_loss = float(config.get('packet_loss', 0.1))
            dedicated_bandwidth = float(config.get('dedicated_bandwidth', 60))
            
            # Service-specific calculations
            metrics = {}
            selected_services = config.get('selected_services', ['datasync'])
            
            # DataSync calculations (enhanced original)
            if 'datasync' in selected_services:
                throughput_result = self.calculator.calculate_enterprise_throughput(
                    config['datasync_instance_type'], config['num_datasync_agents'], config['avg_file_size'], 
                    config['dx_bandwidth_mbps'], config['network_latency'], config['network_jitter'], 
                    config['packet_loss'], config['qos_enabled'], config['dedicated_bandwidth'], 
                    config.get('real_world_mode', True), config.get('network_pattern', 'direct_connect_dedicated')
                )
                
                if len(throughput_result) == 4:
                    datasync_throughput, network_efficiency, theoretical_throughput, real_world_efficiency = throughput_result
                else:
                    datasync_throughput, network_efficiency = throughput_result
                    theoretical_throughput = datasync_throughput * 1.5
                    real_world_efficiency = 0.7
                
                # Apply network optimizations
                tcp_efficiency = {"Default": 1.0, "64KB": 1.05, "128KB": 1.1, "256KB": 1.15, 
                                "512KB": 1.2, "1MB": 1.25, "2MB": 1.3}
                mtu_efficiency = {"1500 (Standard)": 1.0, "9000 (Jumbo Frames)": 1.15, "Custom": 1.1}
                congestion_efficiency = {"Cubic (Default)": 1.0, "BBR": 1.2, "Reno": 0.95, "Vegas": 1.05}
                
                tcp_factor = tcp_efficiency.get(config['tcp_window_size'], 1.0)
                mtu_factor = mtu_efficiency.get(config['mtu_size'], 1.0)
                congestion_factor = congestion_efficiency.get(config['network_congestion_control'], 1.0)
                wan_factor = 1.3 if config['wan_optimization'] else 1.0
                
                optimized_throughput = datasync_throughput * tcp_factor * mtu_factor * congestion_factor * wan_factor
                optimized_throughput = min(optimized_throughput, config['dx_bandwidth_mbps'] * (config['dedicated_bandwidth'] / 100))
                optimized_throughput = max(1, optimized_throughput)
                
                metrics['datasync'] = {
                    'throughput_mbps': optimized_throughput,
                    'efficiency': optimized_throughput / config['dx_bandwidth_mbps'],
                    'theoretical_throughput': theoretical_throughput,
                    'real_world_efficiency': real_world_efficiency,
                    'network_efficiency': network_efficiency
                }
            
            # DMS calculations
            if 'dms' in selected_services and config.get('database_types'):
                dms_result = self.calculator.calculate_dms_throughput(
                    config.get('dms_instance_type', 'dms.c5.large'),
                    config.get('database_size_gb', 5000),
                    config.get('database_types', []),
                    config.get('migration_type', 'full_load_and_cdc'),
                    config.get('network_pattern', 'direct_connect_dedicated'),
                    config['dx_bandwidth_mbps']
                )
                metrics['dms'] = dms_result
            
            # Snowball calculations
            if 'snowball' in selected_services:
                snowball_result = self.calculator.calculate_snowball_timeline(
                    config['data_size_gb'],
                    config.get('snowball_device_type', 'snowball_edge_storage'),
                    config.get('num_snowball_devices', 1),
                    config.get('shipping_location', 'domestic')
                )
                metrics['snowball'] = snowball_result
            
            # Use primary service for main metrics (backward compatibility)
            if 'datasync' in metrics:
                primary_service = 'datasync'
                primary_throughput = metrics['datasync']['throughput_mbps']
            elif 'dms' in metrics:
                primary_service = 'dms'
                primary_throughput = metrics['dms']['throughput_mbps']
            elif 'snowball' in metrics:
                primary_service = 'snowball'
                primary_throughput = metrics['snowball']['throughput_equivalent_mbps']
            else:
                # Fallback
                primary_service = 'datasync'
                primary_throughput = 100
                metrics['datasync'] = {
                    'throughput_mbps': 100,
                    'efficiency': 0.1,
                    'theoretical_throughput': 150,
                    'real_world_efficiency': 0.7,
                    'network_efficiency': 0.7
                }
            
            # Calculate timing
            available_hours_per_day = 16 if config['business_hours_restriction'] else 24
            transfer_days = (effective_data_gb * 8 * 1000) / (primary_throughput * available_hours_per_day * 3600)
            transfer_days = max(0.1, transfer_days)
            
            # Calculate costs
            cost_breakdown = self.calculator.calculate_enterprise_costs(
                config['data_size_gb'], transfer_days, config['datasync_instance_type'], 
                config['num_datasync_agents'], config['compliance_frameworks'], config['s3_storage_class']
            )
            
            # Compliance and business impact (original functionality)
            compliance_reqs, compliance_risks = self.calculator.assess_compliance_requirements(
                config['compliance_frameworks'], config['data_classification'], config['data_residency']
            )
            business_impact = self.calculator.calculate_business_impact(transfer_days, config['data_types'])
            
            # Get AI recommendations (enhanced with multi-service)
            target_region_short = config['target_aws_region'].split()[0]
            networking_recommendations = self.calculator.get_optimal_networking_architecture(
                config['source_location'], target_region_short, config['data_size_gb'],
                config['dx_bandwidth_mbps'], config['database_types'], config['data_types'], config
            )
            
            # Add real AI analysis if enabled
            if config.get('enable_real_ai') and config.get('claude_api_key'):
                ai_analysis = self.calculator.get_real_ai_analysis(config, config['claude_api_key'], config.get('ai_model'))
                if ai_analysis:
                    networking_recommendations['ai_analysis'] = ai_analysis
            
            return {
                'data_size_tb': data_size_tb,
                'effective_data_gb': effective_data_gb,
                'optimized_throughput': primary_throughput,
                'network_efficiency': metrics.get(primary_service, {}).get('network_efficiency', 0.7),
                'transfer_days': transfer_days,
                'cost_breakdown': cost_breakdown,
                'compliance_reqs': compliance_reqs,
                'compliance_risks': compliance_risks,
                'business_impact': business_impact,
                'available_hours_per_day': available_hours_per_day,
                'networking_recommendations': networking_recommendations,
                'service_metrics': metrics,
                'primary_service': primary_service,
                'selected_services': selected_services
            }
            
        except Exception as e:
            st.error(f"Error in calculation: {str(e)}")
            return {
                'data_size_tb': 1.0,
                'effective_data_gb': 1000,
                'optimized_throughput': 100,
                'network_efficiency': 0.7,
                'transfer_days': 10,
                'cost_breakdown': {'total': 1850},
                'compliance_reqs': [],
                'compliance_risks': [],
                'business_impact': {'score': 0.5, 'level': 'Medium'},
                'available_hours_per_day': 24,
                'networking_recommendations': {},
                'service_metrics': {},
                'primary_service': 'datasync',
                'selected_services': ['datasync']
            }
    
    def render_dashboard_tab(self, config, metrics):
        """Enhanced dashboard with multi-service support"""
        st.markdown('<div class="section-header">🏠 Enhanced Enterprise Migration Dashboard</div>', unsafe_allow_html=True)
        
        # Service selection status
        selected_services = config.get('selected_services', ['datasync'])
        st.info(f"🔧 **Active Services:** {', '.join([s.upper() for s in selected_services])}")
        
        # Multi-service comparison if multiple services selected
        if len(selected_services) > 1 and 'service_metrics' in metrics:
            st.markdown('<div class="section-header">📊 Multi-Service Performance Comparison</div>', unsafe_allow_html=True)
            
            comparison_data = []
            for service in selected_services:
                if service in metrics['service_metrics']:
                    service_data = metrics['service_metrics'][service]
                    
                    if service == 'datasync':
                        comparison_data.append({
                            "Service": "DataSync",
                            "Throughput (Mbps)": f"{service_data['throughput_mbps']:.0f}",
                            "Efficiency": f"{service_data['efficiency']:.1%}",
                            "Timeline (Days)": f"{(config['data_size_gb'] * 8 * 1000) / (service_data['throughput_mbps'] * 24 * 3600):.1f}",
                            "Best For": "Files & Objects"
                        })
                    elif service == 'dms':
                        comparison_data.append({
                            "Service": "DMS",
                            "Throughput (Mbps)": f"{service_data['throughput_mbps']:.0f}",
                            "Efficiency": f"{service_data['efficiency']:.1%}",
                            "Timeline (Days)": f"{service_data['full_load_time_hours'] / 24:.1f}",
                            "Best For": "Databases"
                        })
                    elif service == 'snowball':
                        comparison_data.append({
                            "Service": "Snowball",
                            "Throughput (Mbps)": f"{service_data['throughput_equivalent_mbps']:.0f}",
                            "Efficiency": f"{service_data['device_utilization']:.1%}",
                            "Timeline (Days)": f"{service_data['total_timeline_days']:.1f}",
                            "Best For": "Large Datasets"
                        })
            
            if comparison_data:
                df_comparison = pd.DataFrame(comparison_data)
                st.dataframe(df_comparison, use_container_width=True, hide_index=True)
        
        # Executive metrics
        col1, col2, col3, col4, col5 = st.columns(5)
        
        # Calculate dynamic metrics
        active_projects = len(st.session_state.migration_projects) + 1
        total_data_tb = metrics['data_size_tb']
        calculated_success_rate = min(99, 85 + metrics['network_efficiency'] * 15)
        on_premises_cost = metrics['data_size_tb'] * 1000 * 12
        aws_annual_cost = metrics['cost_breakdown']['total'] * 0.1
        annual_savings = max(0, on_premises_cost - aws_annual_cost)
        compliance_score = min(100, 85 + len(config['compliance_frameworks']) * 5)
        
        with col1:
            st.metric("Active Projects", str(active_projects), "+1")
        with col2:
            st.metric("Total Data Volume", f"{total_data_tb:.1f} TB", f"+{metrics['data_size_tb']:.1f} TB")
        with col3:
            st.metric("Migration Success Rate", f"{calculated_success_rate:.0f}%", f"+{calculated_success_rate - 85:.0f}%")
        with col4:
            st.metric("Projected Annual Savings", f"${annual_savings/1000:.0f}K", f"+${annual_savings/1000:.0f}K")
        with col5:
            st.metric("Compliance Score", f"{compliance_score:.0f}%", f"+{compliance_score - 85:.0f}%")
        
        # Current project overview
        st.markdown('<div class="section-header">📊 Current Project Overview</div>', unsafe_allow_html=True)
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            st.metric("💾 Data Volume", f"{metrics['data_size_tb']:.1f} TB", f"{config['data_size_gb']:,.0f} GB")
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col2:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            performance_mode = "Real-world" if config.get('real_world_mode', True) else "Theoretical"
            efficiency_pct = f"{metrics['network_efficiency']:.1%}"
            st.metric("⚡ Throughput", f"{metrics['optimized_throughput']:.0f} Mbps", f"{efficiency_pct} efficiency ({performance_mode})")
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col3:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            timeline_status = "On Track" if metrics['transfer_days'] <= config['max_transfer_days'] else "At Risk"
            timeline_delta = f"{metrics['transfer_days']*24:.0f} hours ({timeline_status})"
            st.metric("📅 Duration", f"{metrics['transfer_days']:.1f} days", timeline_delta)
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col4:
            st.markdown('<div class="metric-card">', unsafe_allow_html=True)
            budget_status = "Under Budget" if metrics['cost_breakdown']['total'] <= config['budget_allocated'] else "Over Budget"
            budget_delta = f"${metrics['cost_breakdown']['total']/metrics['data_size_tb']:.0f}/TB ({budget_status})"
            st.metric("💰 Total Cost", f"${metrics['cost_breakdown']['total']:,.0f}", budget_delta)
            st.markdown('</div>', unsafe_allow_html=True)
        
        # AI Recommendations
        st.markdown('<div class="section-header">🤖 AI-Powered Multi-Service Recommendations</div>', unsafe_allow_html=True)
        recommendations = metrics['networking_recommendations']
        
        # Primary recommendation
        ai_type = "Real-time Claude AI" if config.get('enable_real_ai') and config.get('claude_api_key') else "Built-in AI"
        
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            # Enhanced performance analysis
            primary_service = metrics.get('primary_service', 'datasync')
            
            st.markdown(f"""
            <div class="ai-insight">
                <strong>🧠 {ai_type} Analysis:</strong> {recommendations.get('rationale', 'Analysis in progress...')}<br><br>
                <strong>🎯 Primary Service Recommendation:</strong> {recommendations.get('primary_method', primary_service.upper())}<br>
                <strong>📊 Selected Services Performance:</strong> {', '.join([s.upper() for s in selected_services])}
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown("**🎯 AI Recommendations**")
            st.write(f"**Primary Method:** {recommendations.get('primary_method', 'DataSync')}")
            st.write(f"**Network:** {recommendations.get('networking_option', 'Direct Connect')}")
            st.write(f"**Risk Level:** {recommendations.get('risk_level', 'Medium')}")
            st.write(f"**Cost Efficiency:** {recommendations.get('cost_efficiency', 'Medium')}")
        
        with col3:
            st.markdown("**⚡ Expected Performance**")
            ai_perf = recommendations.get('estimated_performance', {})
            st.write(f"**Throughput:** {ai_perf.get('throughput_mbps', 0):.0f} Mbps")
            st.write(f"**Duration:** {ai_perf.get('estimated_days', 0):.1f} days")
            st.write(f"**Network Eff:** {ai_perf.get('network_efficiency', 0):.1%}")
            st.write(f"**Primary Service:** {metrics.get('primary_service', 'datasync').upper()}")
        
# REPLACE the service-specific recommendations section with this PURE STREAMLIT version:

        # Service-specific recommendations (PURE STREAMLIT VERSION)
        if 'service_recommendations' in recommendations:
            st.markdown("### 🔧 Service-Specific Analysis")
            st.info(f"📊 Analysis for {config['data_size_gb']:,} GB dataset with {config['dx_bandwidth_mbps']:,} Mbps bandwidth")
            
            # Calculate number of columns based on services
            num_services = len(recommendations['service_recommendations'])
            if num_services == 1:
                service_cols = st.columns([1])
            elif num_services == 2:
                service_cols = st.columns(2)
            else:
                service_cols = st.columns(min(3, num_services))
            
            for idx, (service, rec) in enumerate(recommendations['service_recommendations'].items()):
                # Use modulo to wrap columns if more than 3 services
                col_idx = idx % len(service_cols)
                
                with service_cols[col_idx]:
                    # Fix timeline display with better logic
                    estimated_days = rec.get('estimated_days', 0)
                    if estimated_days < 0.1:
                        timeline_display = "< 3 hours"
                    elif estimated_days < 1:
                        timeline_display = f"{estimated_days * 24:.1f} hours"
                    elif estimated_days < 2:
                        timeline_display = f"{estimated_days:.1f} day"
                    else:
                        timeline_display = f"{estimated_days:.1f} days"
                    
                    # Fix throughput display with better units
                    throughput_mbps = rec.get('throughput_mbps', 0)
                    if throughput_mbps >= 1000:
                        throughput_display = f"{throughput_mbps/1000:.1f} Gbps"
                    else:
                        throughput_display = f"{throughput_mbps:.0f} Mbps"
                    
                    # Use Streamlit's built-in container for clean display
                    with st.container():
                        # Service header with suitability badge
                        suitability = rec.get('suitability', 'Medium')
                        if suitability == 'High':
                            st.success(f"**{service.upper()}** - {suitability} Suitability")
                        elif suitability == 'Medium':
                            st.warning(f"**{service.upper()}** - {suitability} Suitability")
                        else:
                            st.error(f"**{service.upper()}** - {suitability} Suitability")
                        
                        # Performance metrics in a clean format
                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.metric("Throughput", throughput_display)
                        with col_b:
                            st.metric("Timeline", timeline_display)
                        
                        # Best use cases
                        pros = rec.get('pros', ['General use'])
                        st.caption(f"💡 Best for: {', '.join(pros[:2])}")
                        
                        st.divider()  # Add visual separation between services
        
        # Real AI Analysis if available
        if recommendations.get('ai_analysis'):
            st.markdown('<div class="section-header">🔮 Advanced Claude AI Analysis</div>', unsafe_allow_html=True)
            st.markdown(f"""
            <div class="ai-insight">
                <strong>🤖 Real-time Claude AI Insights:</strong><br>
                {recommendations['ai_analysis'].replace('\n', '<br>')}
            </div>
            """, unsafe_allow_html=True)
        
        # Real-time activities
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown('<div class="section-header">📋 Real-time Activities</div>', unsafe_allow_html=True)
            
            current_time = datetime.now().strftime("%H:%M")
            activities = [
                f"🕐 {current_time} - {config['project_name']} configuration updated",
                f"🔧 Services enabled: {', '.join([s.upper() for s in selected_services])}",
                f"🤖 AI recommended: {recommendations.get('primary_method', 'DataSync')} for {metrics['data_size_tb']:.1f}TB dataset",
                f"🌐 Network analysis: {recommendations.get('networking_option', 'Direct Connect')} ({metrics['optimized_throughput']:.0f} Mbps)",
                f"📊 Business impact: {metrics['business_impact']['level']} priority",
                f"🔒 {len(config['compliance_frameworks'])} compliance framework(s) validated",
                f"💰 Cost analysis: ${metrics['cost_breakdown']['total']:,.0f} total budget",
                f"⚡ Performance mode: {'Real-world modeling' if config.get('real_world_mode') else 'Theoretical maximum'}"
            ]
            
            for activity in activities:
                st.write(f"• {activity}")
        
        with col2:
            st.markdown('<div class="section-header">⚠️ Real-time Alerts & Status</div>', unsafe_allow_html=True)
            
            alerts = []
            
            # Multi-service alerts
            if len(selected_services) == 1:
                alerts.append("🟡 Single service selected - consider multi-service analysis")
            elif len(selected_services) > 3:
                alerts.append("🟡 Multiple services selected - review recommendations carefully")
            
            # Timeline and budget alerts
            if metrics['transfer_days'] > config['max_transfer_days']:
                days_over = metrics['transfer_days'] - config['max_transfer_days']
                alerts.append(f"🔴 Timeline risk: {days_over:.1f} days over {config['max_transfer_days']}-day target")
            
            if metrics['cost_breakdown']['total'] > config['budget_allocated']:
                over_budget = metrics['cost_breakdown']['total'] - config['budget_allocated']
                alerts.append(f"🔴 Budget exceeded by ${over_budget:,.0f}")
            
            # Service-specific alerts
            if 'dms' in selected_services and not config.get('database_types'):
                alerts.append("🟡 DMS selected but no database types specified")
            
            if 'snowball' in selected_services and metrics['data_size_tb'] < 1:
                alerts.append("🟡 Snowball may be overkill for datasets under 1TB")
            
            # Performance alerts
            if metrics['network_efficiency'] < 0.5:
                alerts.append("🟡 Low network efficiency - consider optimization")
            
            # Compliance alerts
            if config['data_classification'] in ["Restricted", "Top Secret"] and not config['encryption_at_rest']:
                alerts.append("🔴 Critical: Encryption at rest required for classified data")
            
            if not alerts:
                alerts.append("🟢 All systems optimal - no issues detected")
            
            for alert in alerts:
                st.write(alert)
    
    def render_multiservice_tab(self, config, metrics):
        """Dedicated multi-service comparison tab"""
        st.markdown('<div class="section-header">🔧 Multi-Service Migration Analysis</div>', unsafe_allow_html=True)
        
        selected_services = config.get('selected_services', ['datasync'])
        
        if len(selected_services) < 2:
            st.warning("Select at least 2 services for meaningful multi-service analysis.")
            st.info("Use the sidebar to enable additional services like DMS, Snowball, or Network Pattern Analysis.")
            return
        
        # Service comparison matrix
        st.subheader("📊 Service Performance Matrix")
        
        if 'service_metrics' in metrics:
            comparison_data = []
            
            for service in selected_services:
                if service in metrics['service_metrics']:
                    service_data = metrics['service_metrics'][service]
                    
                    if service == 'datasync':
                        comparison_data.append({
                            "Service": "DataSync",
                            "Throughput (Mbps)": service_data['throughput_mbps'],
                            "Efficiency (%)": service_data['efficiency'] * 100,
                            "Estimated Days": (config['data_size_gb'] * 8 * 1000) / (service_data['throughput_mbps'] * 24 * 3600),
                            "Cost Factor": 1.0,
                            "Use Case": "Files & Objects",
                            "Incremental": "Yes"
                        })
                    elif service == 'dms':
                        comparison_data.append({
                            "Service": "DMS",
                            "Throughput (Mbps)": service_data['throughput_mbps'],
                            "Efficiency (%)": service_data['efficiency'] * 100,
                            "Estimated Days": service_data['full_load_time_hours'] / 24,
                            "Cost Factor": 1.2,
                            "Use Case": "Databases",
                            "Incremental": "Yes (CDC)"
                        })
                    elif service == 'snowball':
                        comparison_data.append({
                            "Service": "Snowball",
                            "Throughput (Mbps)": service_data['throughput_equivalent_mbps'],
                            "Efficiency (%)": service_data['device_utilization'] * 100,
                            "Estimated Days": service_data['total_timeline_days'],
                            "Cost Factor": 0.6,
                            "Use Case": "Large Datasets",
                            "Incremental": "No"
                        })
            
            if comparison_data:
                df_comparison = pd.DataFrame(comparison_data)
                
                # Display detailed comparison table
                st.dataframe(df_comparison, use_container_width=True, hide_index=True)
                
                # Performance comparison charts
                col1, col2 = st.columns(2)
                
                with col1:
                    fig_throughput = px.bar(
                        df_comparison,
                        x="Service",
                        y="Throughput (Mbps)",
                        title="Throughput Comparison",
                        color="Service",
                        color_discrete_map={
                            "DataSync": "#FF9900",
                            "DMS": "#007bff", 
                            "Snowball": "#28a745"
                        }
                    )
                    st.plotly_chart(fig_throughput, use_container_width=True)
                
                with col2:
                    fig_timeline = px.bar(
                        df_comparison,
                        x="Service",
                        y="Estimated Days",
                        title="Timeline Comparison",
                        color="Service",
                        color_discrete_map={
                            "DataSync": "#FF9900",
                            "DMS": "#007bff",
                            "Snowball": "#28a745"
                        }
                    )
                    st.plotly_chart(fig_timeline, use_container_width=True)
                
                # 3D Performance Analysis
                st.subheader("🎯 3D Performance Analysis")
                
                fig_3d = px.scatter_3d(
                    df_comparison,
                    x="Throughput (Mbps)",
                    y="Estimated Days", 
                    z="Efficiency (%)",
                    color="Service",
                    size="Cost Factor",
                    hover_data=["Use Case", "Incremental"],
                    title="3D Service Performance Analysis",
                    color_discrete_map={
                        "DataSync": "#FF9900",
                        "DMS": "#007bff",
                        "Snowball": "#28a745"
                    }
                )
                st.plotly_chart(fig_3d, use_container_width=True)
        
        # Service recommendations
        if 'service_recommendations' in metrics.get('networking_recommendations', {}):
            st.subheader("🤖 AI Service Recommendations")
            
            recommendations = metrics['networking_recommendations']['service_recommendations']
            
            rec_cols = st.columns(len(recommendations))
            for idx, (service, rec) in enumerate(recommendations.items()):
                with rec_cols[idx]:
                    suitability_color = {
                        "High": "#28a745",
                        "Medium": "#ffc107",
                        "Low": "#dc3545"
                    }.get(rec.get('suitability', 'Medium'), "#ffc107")
                    
                    st.markdown(f"""
                    <div class="service-card" style="border-left-color: {suitability_color};">
                        <h4>{service.upper()} Analysis</h4>
                        <p><strong>Suitability:</strong> <span style="color: {suitability_color};">{rec.get('suitability', 'Medium')}</span></p>
                        <p><strong>Pros:</strong></p>
                        <ul>
                    """, unsafe_allow_html=True)
                    
                    for pro in rec.get('pros', []):
                        st.markdown(f"<li>{pro}</li>", unsafe_allow_html=True)
                    
                    st.markdown("""
                        </ul>
                        <p><strong>Cons:</strong></p>
                        <ul>
                    """, unsafe_allow_html=True)
                    
                    for con in rec.get('cons', []):
                        st.markdown(f"<li>{con}</li>", unsafe_allow_html=True)
                    
                    st.markdown("</ul></div>", unsafe_allow_html=True)
    
    def render_network_tab(self, config, metrics):
        """Enhanced network analysis with multi-service support"""
        st.markdown('<div class="section-header">🌐 Enhanced Network Analysis & Architecture</div>', unsafe_allow_html=True)
        
        # Network pattern analysis
        selected_services = config.get('selected_services', ['datasync'])
        
        # Network performance dashboard
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            utilization_pct = (metrics['optimized_throughput'] / config['dx_bandwidth_mbps']) * 100
            st.metric("Network Utilization", f"{utilization_pct:.1f}%", f"{metrics['optimized_throughput']:.0f} Mbps")
        
        with col2:
            efficiency_vs_max = metrics['network_efficiency'] * 100
            st.metric("Network Efficiency", f"{efficiency_vs_max:.1f}%", "vs maximum")
        
        with col3:
            st.metric("Network Pattern", config.get('network_pattern', 'direct_connect_dedicated').replace('_', ' ').title())
        
        with col4:
            st.metric("Latency Impact", f"{config['network_latency']} ms", "to target region")
        
        # Multi-service network impact
        if 'service_metrics' in metrics and len(selected_services) > 1:
            st.subheader("🔗 Network Impact by Service")
            
            network_impact_data = []
            for service in selected_services:
                if service in metrics['service_metrics']:
                    service_data = metrics['service_metrics'][service]
                    
                    # Calculate network sensitivity
                    if service == 'datasync':
                        latency_sensitivity = "High"
                        bandwidth_dependency = "Very High"
                        network_score = service_data.get('efficiency', 0.7) * 100
                    elif service == 'dms':
                        latency_sensitivity = "Medium"
                        bandwidth_dependency = "Medium" 
                        network_score = service_data.get('network_efficiency', 0.8) * 100
                    elif service == 'snowball':
                        latency_sensitivity = "None"
                        bandwidth_dependency = "None"
                        network_score = 95  # Not network dependent
                    else:
                        latency_sensitivity = "Medium"
                        bandwidth_dependency = "Medium"
                        network_score = 75
                    
                    network_impact_data.append({
                        "Service": service.upper(),
                        "Latency Sensitivity": latency_sensitivity,
                        "Bandwidth Dependency": bandwidth_dependency,
                        "Network Score": network_score,
                        "Pattern Efficiency": f"{network_score:.0f}%"
                    })
            
            if network_impact_data:
                df_network = pd.DataFrame(network_impact_data)
                st.dataframe(df_network, use_container_width=True, hide_index=True)
                
                # Network sensitivity chart
                fig_network = px.bar(
                    df_network,
                    x="Service",
                    y="Network Score",
                    color="Bandwidth Dependency",
                    title="Network Dependency by Service",
                    color_discrete_map={
                        "Very High": "#dc3545",
                        "High": "#ffc107",
                        "Medium": "#17a2b8",
                        "None": "#28a745"
                    }
                )
                st.plotly_chart(fig_network, use_container_width=True)
        
        # Network pattern comparison
        st.subheader("🌐 Network Pattern Analysis")
        
        # Calculate performance for different patterns
        patterns = ["direct_connect_dedicated", "direct_connect_hosted", "site_to_site_vpn", "transit_gateway"]
        pattern_comparison = []
        
        for pattern in patterns:
            pattern_info = self.calculator.network_patterns[pattern]
            
            # Estimate throughput for this pattern
            max_bandwidth = min(config['dx_bandwidth_mbps'], pattern_info['max_bandwidth_gbps'] * 1000)
            pattern_throughput = max_bandwidth * pattern_info['efficiency']
            
            # Calculate cost (simplified)
            monthly_cost = (config['dx_bandwidth_mbps'] / 1000) * pattern_info['monthly_cost_per_gbps']
            
            pattern_comparison.append({
                "Network Pattern": pattern.replace('_', ' ').title(),
                "Max Bandwidth (Gbps)": pattern_info['max_bandwidth_gbps'],
                "Estimated Throughput (Mbps)": pattern_throughput,
                "Latency (ms)": pattern_info['latency_ms'],
                "Setup Time (Days)": pattern_info['setup_time_days'],
                "Monthly Cost ($)": monthly_cost,
                "Availability (%)": pattern_info['availability'],
                "Current": "✅" if pattern == config.get('network_pattern') else ""
            })
        
        if pattern_comparison:
            df_patterns = pd.DataFrame(pattern_comparison)
            st.dataframe(df_patterns, use_container_width=True, hide_index=True)
            
            # Pattern performance visualization
            fig_pattern = px.scatter(
                df_patterns,
                x="Setup Time (Days)",
                y="Estimated Throughput (Mbps)",
                size="Monthly Cost ($)",
                color="Network Pattern",
                hover_data=["Latency (ms)", "Availability (%)"],
                title="Network Pattern Performance vs Setup Time"
            )
            st.plotly_chart(fig_pattern, use_container_width=True)
        
        # AI Recommendations
        recommendations = metrics['networking_recommendations']
        st.subheader("🤖 AI Network Architecture Recommendations")
        
        st.markdown(f"""
        <div class="ai-insight">
            <strong>🧠 AI Network Analysis:</strong> {recommendations.get('rationale', 'Analysis in progress...')}<br><br>
            <strong>🌐 Recommended Pattern:</strong> {recommendations.get('networking_option', 'Direct Connect')}<br>
            <strong>📊 Optimal for Services:</strong> {', '.join([s.upper() for s in selected_services])}
        </div>
        """, unsafe_allow_html=True)
    
    def render_performance_tab(self, config, metrics):
        """Enhanced performance tab with multi-service optimization"""
        st.markdown('<div class="section-header">⚡ Multi-Service Performance Optimization</div>', unsafe_allow_html=True)
        
        selected_services = config.get('selected_services', ['datasync'])
        
        # Performance metrics comparison
        col1, col2, col3, col4 = st.columns(4)
        
        # Calculate baseline and improvements
        with col1:
            st.metric("Primary Service", metrics.get('primary_service', 'datasync').upper())
        
        with col2:
            st.metric("Network Efficiency", f"{metrics['network_efficiency']:.1%}")
        
        with col3:
            st.metric("Transfer Time", f"{metrics['transfer_days']:.1f} days")
        
        with col4:
            st.metric("Cost per TB", f"${metrics['cost_breakdown']['total']/metrics['data_size_tb']:.0f}")
        
        # Service-specific performance analysis
        if 'service_metrics' in metrics and len(selected_services) > 1:
            st.subheader("🔧 Service Performance Breakdown")
            
            performance_data = []
            for service in selected_services:
                if service in metrics['service_metrics']:
                    service_data = metrics['service_metrics'][service]
                    
                    if service == 'datasync':
                        performance_data.append({
                            "Service": "DataSync",
                            "Throughput (Mbps)": service_data['throughput_mbps'],
                            "Efficiency (%)": service_data['efficiency'] * 100,
                            "Optimization Potential": "High" if service_data['efficiency'] < 0.8 else "Medium",
                            "Bottleneck": "Network/Storage" if service_data['efficiency'] < 0.6 else "None"
                        })
                    elif service == 'dms':
                        performance_data.append({
                            "Service": "DMS", 
                            "Throughput (Mbps)": service_data['throughput_mbps'],
                            "Efficiency (%)": service_data['efficiency'] * 100,
                            "Optimization Potential": "Medium",
                            "Bottleneck": "Database I/O" if service_data['database_compatibility'] < 0.9 else "None"
                        })
                    elif service == 'snowball':
                        performance_data.append({
                            "Service": "Snowball",
                            "Throughput (Mbps)": service_data['throughput_equivalent_mbps'],
                            "Efficiency (%)": service_data['device_utilization'] * 100,
                            "Optimization Potential": "Low",
                            "Bottleneck": "Physical Logistics"
                        })
            
            if performance_data:
                df_performance = pd.DataFrame(performance_data)
                st.dataframe(df_performance, use_container_width=True, hide_index=True)
        
        # Optimization recommendations
        st.subheader("🎯 Performance Optimization Recommendations")
        
        optimization_recommendations = []
        
        # DataSync optimizations
        if 'datasync' in selected_services:
            if config['tcp_window_size'] == "Default":
                optimization_recommendations.append("🔧 Enable TCP window scaling (2MB) for 25-30% improvement")
            
            if config['mtu_size'] == "1500 (Standard)":
                optimization_recommendations.append("📡 Configure jumbo frames (9000 MTU) for 10-15% improvement")
            
            if not config['wan_optimization']:
                optimization_recommendations.append("🚀 Enable WAN optimization for 25-30% improvement")
        
        # DMS optimizations
        if 'dms' in selected_services:
            if config.get('migration_type') == 'full_load' and config.get('database_types'):
                optimization_recommendations.append("🗄️ Consider full_load_and_cdc for minimal downtime")
            
            if config.get('dms_instance_type', '').startswith('dms.t3'):
                optimization_recommendations.append("⚡ Upgrade to compute-optimized DMS instance for better performance")
        
        # Snowball optimizations
        if 'snowball' in selected_services:
            if config.get('num_snowball_devices', 1) == 1 and metrics['data_size_tb'] > 50:
                optimization_recommendations.append("📦 Use multiple Snowball devices for parallel transfer")
        
        # Network optimizations
        if config.get('network_pattern') == 'site_to_site_vpn' and metrics['data_size_tb'] > 10:
            optimization_recommendations.append("🌐 Upgrade to Direct Connect for better performance")
        
        if optimization_recommendations:
            for rec in optimization_recommendations:
                st.write(f"• {rec}")
        else:
            st.success("✅ Configuration is well optimized across all selected services!")
        
        # Performance comparison chart
        st.subheader("📊 Service Performance Comparison")
        
        if 'service_metrics' in metrics and len(selected_services) > 1:
            # Create performance comparison visualization
            services = []
            throughputs = []
            efficiencies = []
            
            for service in selected_services:
                if service in metrics['service_metrics']:
                    service_data = metrics['service_metrics'][service]
                    services.append(service.upper())
                    
                    if service == 'datasync':
                        throughputs.append(service_data['throughput_mbps'])
                        efficiencies.append(service_data['efficiency'] * 100)
                    elif service == 'dms':
                        throughputs.append(service_data['throughput_mbps'])
                        efficiencies.append(service_data['efficiency'] * 100)
                    elif service == 'snowball':
                        throughputs.append(service_data['throughput_equivalent_mbps'])
                        efficiencies.append(service_data['device_utilization'] * 100)
            
            if services:
                fig_comparison = go.Figure()
                
                # Add throughput bars
                fig_comparison.add_trace(go.Bar(
                    x=services,
                    y=throughputs,
                    name='Throughput (Mbps)',
                    marker_color='#FF9900',
                    yaxis='y',
                    offsetgroup=1
                ))
                
                # Add efficiency bars on secondary y-axis
                fig_comparison.add_trace(go.Bar(
                    x=services,
                    y=efficiencies,
                    name='Efficiency (%)',
                    marker_color='#007bff',
                    yaxis='y2',
                    offsetgroup=2
                ))
                
                # Update layout
                fig_comparison.update_layout(
                    title='Service Performance Comparison',
                    xaxis_title='Service',
                    yaxis=dict(title='Throughput (Mbps)', side='left'),
                    yaxis2=dict(title='Efficiency (%)', side='right', overlaying='y'),
                    barmode='group',
                    height=500
                )
                
                st.plotly_chart(fig_comparison, use_container_width=True)
    
    def render_security_tab(self, config, metrics):
        """Enhanced security tab with multi-service compliance"""
        st.markdown('<div class="section-header">🔒 Multi-Service Security & Compliance</div>', unsafe_allow_html=True)
        
        selected_services = config.get('selected_services', ['datasync'])
        
        # Security dashboard
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            security_score = 85 + (10 if config['encryption_in_transit'] else 0) + (5 if len(config['compliance_frameworks']) > 0 else 0)
            st.metric("Security Score", f"{security_score}/100")
        
        with col2:
            compliance_score = min(100, len(config['compliance_frameworks']) * 15)
            st.metric("Compliance Coverage", f"{compliance_score}%")
        
        with col3:
            data_risk_level = {"Public": "Low", "Internal": "Medium", "Confidential": "High", "Restricted": "Very High", "Top Secret": "Critical"}
            st.metric("Data Risk Level", data_risk_level.get(config['data_classification'], "Medium"))
        
        with col4:
            st.metric("Audit Events", len(st.session_state.audit_log))
        
        # Service-specific security analysis
        st.subheader("🛡️ Service Security Analysis")
        
        security_analysis = []
        for service in selected_services:
            if service == 'datasync':
                security_analysis.append({
                    "Service": "DataSync",
                    "Encryption in Transit": "✅" if config['encryption_in_transit'] else "❌",
                    "Encryption at Rest": "✅" if config['encryption_at_rest'] else "❌",
                    "Network Security": "High (VPC Endpoints)",
                    "Audit Logging": "CloudTrail + DataSync Logs",
                    "Compliance Ready": "✅" if config['compliance_frameworks'] else "⚠️"
                })
            elif service == 'dms':
                security_analysis.append({
                    "Service": "DMS",
                    "Encryption in Transit": "✅ (SSL/TLS)",
                    "Encryption at Rest": "✅" if config['encryption_at_rest'] else "❌",
                    "Network Security": "High (VPC + Security Groups)",
                    "Audit Logging": "CloudTrail + DMS Logs",
                    "Compliance Ready": "✅" if config['compliance_frameworks'] else "⚠️"
                })
            elif service == 'snowball':
                security_analysis.append({
                    "Service": "Snowball",
                    "Encryption in Transit": "✅ (Physical Security)",
                    "Encryption at Rest": "✅ (256-bit Encryption)",
                    "Network Security": "N/A (Offline Transfer)",
                    "Audit Logging": "Chain of Custody",
                    "Compliance Ready": "✅ (FIPS 140-2)"
                })
        
        if security_analysis:
            df_security = pd.DataFrame(security_analysis)
            st.dataframe(df_security, use_container_width=True, hide_index=True)
        
        # Compliance framework analysis
        if config['compliance_frameworks']:
            st.subheader("🏛️ Compliance Framework Analysis")
            
            compliance_analysis = []
            for framework in config['compliance_frameworks']:
                if framework in self.calculator.compliance_requirements:
                    reqs = self.calculator.compliance_requirements[framework]
                    
                    # Check compliance status for each service
                    service_compliance = []
                    for service in selected_services:
                        if service == 'datasync':
                            compliance_status = "✅" if config['encryption_in_transit'] and config['encryption_at_rest'] else "⚠️"
                        elif service == 'dms':
                            compliance_status = "✅" if config['encryption_at_rest'] else "⚠️"
                        elif service == 'snowball':
                            compliance_status = "✅"  # Snowball is generally compliant
                        else:
                            compliance_status = "⚠️"
                        service_compliance.append(f"{service.upper()}: {compliance_status}")
                    
                    compliance_analysis.append({
                        "Framework": framework,
                        "Requirements": ", ".join(reqs.keys()),
                        "Service Compliance": " | ".join(service_compliance),
                        "Overall Status": "✅" if all("✅" in sc for sc in service_compliance) else "⚠️"
                    })
            
            if compliance_analysis:
                df_compliance = pd.DataFrame(compliance_analysis)
                st.dataframe(df_compliance, use_container_width=True, hide_index=True)
        
        # AI Security recommendations
        st.subheader("🤖 AI Security Recommendations")
        
        recommendations = metrics['networking_recommendations']
        security_recommendations = []
        
        # Service-specific security recommendations
        if 'datasync' in selected_services and not config['encryption_in_transit']:
            security_recommendations.append("🔒 Enable encryption in transit for DataSync transfers")
        
        if 'dms' in selected_services and config.get('migration_type') == 'cdc_only':
            security_recommendations.append("🗄️ Ensure CDC encryption for sensitive database changes")
        
        if config['data_classification'] in ['Restricted', 'Top Secret'] and 'snowball' in selected_services:
            security_recommendations.append("📦 Snowball provides FIPS 140-2 Level 2 encryption suitable for classified data")
        
        # Network security recommendations
        if config.get('network_pattern') == 'site_to_site_vpn':
            security_recommendations.append("🌐 VPN provides encrypted tunnel - suitable for most compliance requirements")
        elif 'direct_connect' in config.get('network_pattern', ''):
            security_recommendations.append("🌐 Direct Connect requires VPN overlay for encrypted transit")
        
        if security_recommendations:
            for rec in security_recommendations:
                st.write(f"• {rec}")
        else:
            st.success("✅ Security configuration meets requirements for all selected services!")
    
    def render_analytics_tab(self, config, metrics):
        """Enhanced analytics with multi-service insights"""
        st.markdown('<div class="section-header">📈 Multi-Service Analytics & Insights</div>', unsafe_allow_html=True)
        
        selected_services = config.get('selected_services', ['datasync'])
        
        # ROI Analysis
        st.subheader("💡 Multi-Service ROI Analysis")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            on_premises_annual_cost = metrics['data_size_tb'] * 1000 * 12
            aws_annual_cost = metrics['cost_breakdown']['total'] * 0.1
            annual_savings = max(0, on_premises_annual_cost - aws_annual_cost)
            st.metric("Annual Savings", f"${annual_savings:,.0f}")
        
        with col2:
            roi_percentage = (annual_savings / metrics['cost_breakdown']['total']) * 100 if metrics['cost_breakdown']['total'] > 0 else 0
            st.metric("ROI", f"{roi_percentage:.1f}%")
        
        with col3:
            payback_period = metrics['cost_breakdown']['total'] / annual_savings if annual_savings > 0 else 0
            payback_display = f"{payback_period:.1f} years" if payback_period > 0 and payback_period < 50 else "N/A"
            st.metric("Payback Period", payback_display)
        
        # Service cost comparison
        if len(selected_services) > 1:
            st.subheader("💰 Service Cost Analysis")
            
            # Estimate costs for each service
            service_costs = []
            for service in selected_services:
                if service == 'datasync':
                    # DataSync costs
                    instance_cost = self.calculator.instance_performance[config['datasync_instance_type']]['cost_hour']
                    service_cost = instance_cost * config['num_datasync_agents'] * 24 * metrics['transfer_days']
                    service_cost += config['data_size_gb'] * 0.0125  # DataSync service fee
                    
                elif service == 'dms':
                    # DMS costs
                    dms_cost = self.calculator.dms_performance[config.get('dms_instance_type', 'dms.c5.large')]['cost_hour']
                    if 'dms' in metrics.get('service_metrics', {}):
                        dms_days = metrics['service_metrics']['dms']['full_load_time_hours'] / 24
                    else:
                        dms_days = 7  # Estimate
                    service_cost = dms_cost * 24 * dms_days
                    
                elif service == 'snowball':
                    # Snowball costs
                    if 'snowball' in metrics.get('service_metrics', {}):
                        service_cost = metrics['service_metrics']['snowball']['total_cost']
                    else:
                        service_cost = 300  # Estimate
                
                service_costs.append({
                    "Service": service.upper(),
                    "Estimated Cost": service_cost,
                    "Cost per TB": service_cost / metrics['data_size_tb'],
                    "Relative Cost": "Low" if service_cost < 5000 else "Medium" if service_cost < 15000 else "High"
                })
            
            if service_costs:
                df_costs = pd.DataFrame(service_costs)
                st.dataframe(df_costs, use_container_width=True, hide_index=True)
                
                # Cost comparison chart
                fig_costs = px.bar(
                    df_costs,
                    x="Service",
                    y="Estimated Cost",
                    color="Relative Cost",
                    title="Service Cost Comparison",
                    color_discrete_map={
                        "Low": "#28a745",
                        "Medium": "#ffc107",
                        "High": "#dc3545"
                    }
                )
                st.plotly_chart(fig_costs, use_container_width=True)
        
        # Performance trends
        st.subheader("📊 Performance Trends Analysis")
        
        # Generate realistic performance trends
        dates = pd.date_range(start="2024-01-01", end="2024-12-31", freq="M")
        
        trend_data = []
        base_throughput = metrics['optimized_throughput']
        
        for i, date in enumerate(dates):
            # Simulate improvement over time
            improvement_factor = 1.0 + (i * 0.02)  # 2% improvement per month
            seasonal_factor = 0.95 if date.month in [11, 12, 1] else 1.0  # Holiday slowdown
            
            for service in selected_services:
                if service in metrics.get('service_metrics', {}):
                    if service == 'datasync':
                        throughput = metrics['service_metrics']['datasync']['throughput_mbps']
                    elif service == 'dms':
                        throughput = metrics['service_metrics']['dms']['throughput_mbps']
                    elif service == 'snowball':
                        throughput = metrics['service_metrics']['snowball']['throughput_equivalent_mbps']
                    else:
                        throughput = base_throughput
                    
                    adjusted_throughput = throughput * improvement_factor * seasonal_factor
                    
                    trend_data.append({
                        "Date": date,
                        "Service": service.upper(),
                        "Throughput": adjusted_throughput,
                        "Type": "Historical"
                    })
        
        # Add future predictions
        future_dates = pd.date_range(start="2025-01-01", end="2025-06-30", freq="M")
        for date in future_dates:
            for service in selected_services:
                if service in metrics.get('service_metrics', {}):
                    if service == 'datasync':
                        throughput = metrics['service_metrics']['datasync']['throughput_mbps']
                    elif service == 'dms':
                        throughput = metrics['service_metrics']['dms']['throughput_mbps']
                    elif service == 'snowball':
                        throughput = metrics['service_metrics']['snowball']['throughput_equivalent_mbps']
                    else:
                        throughput = base_throughput
                    
                    # Future improvements
                    future_improvement = throughput * 1.5  # 50% improvement with optimizations
                    
                    trend_data.append({
                        "Date": date,
                        "Service": service.upper(),
                        "Throughput": future_improvement,
                        "Type": "Predicted"
                    })
        
        if trend_data:
            df_trends = pd.DataFrame(trend_data)
            
            fig_trends = px.line(
                df_trends,
                x="Date",
                y="Throughput",
                color="Service",
                line_dash="Type",
                title="Service Performance Trends & Predictions",
                labels={"Throughput": "Throughput (Mbps)"}
            )
            
            fig_trends.add_vline(
                x=pd.Timestamp("2025-01-01"),
                line_dash="dash",
                line_color="red",
                annotation_text="Future Predictions"
            )
            
            st.plotly_chart(fig_trends, use_container_width=True)
        
        # AI Business Impact Analysis
        recommendations = metrics['networking_recommendations']
        st.subheader("🤖 AI Business Impact Analysis")
        
        st.markdown(f"""
        <div class="recommendation-box">
            <h4>Multi-Service Strategic Impact</h4>
            <p><strong>Primary Recommendation:</strong> {recommendations.get('primary_method', 'DataSync')} delivers optimal performance for your {metrics['data_size_tb']:.1f}TB dataset</p>
            <p><strong>Service Portfolio:</strong> {len(selected_services)} services analyzed provide comprehensive migration coverage</p>
            <p><strong>Risk Mitigation:</strong> Multi-service approach reduces single point of failure risks</p>
            <p><strong>Cost Optimization:</strong> Service selection optimized for {recommendations.get('cost_efficiency', 'medium')} cost efficiency</p>
        </div>
        """, unsafe_allow_html=True)
    
    def render_conclusion_tab(self, config, metrics):
        """Enhanced conclusion with multi-service recommendations"""
        st.title("🎯 Multi-Service Migration Strategy & Executive Decision")
        
        selected_services = config.get('selected_services', ['datasync'])
        recommendations = metrics['networking_recommendations']
        
        # Calculate overall recommendation score
        performance_score = min(100, (metrics['optimized_throughput'] / 10))
        cost_score = min(50, max(0, 50 - (metrics['cost_breakdown']['total'] / config['budget_allocated'] - 1) * 100))
        timeline_score = min(30, max(0, 30 - (metrics['transfer_days'] / config['max_transfer_days'] - 1) * 100))
        risk_score = {"Low": 20, "Medium": 15, "High": 10, "Critical": 5}.get(recommendations.get('risk_level', 'Medium'), 15)
        service_bonus = len(selected_services) * 5  # Bonus for multi-service analysis
        
        overall_score = performance_score + cost_score + timeline_score + risk_score + service_bonus
        
        # Determine strategy status
        if overall_score >= 150:
            strategy_status = "✅ HIGHLY RECOMMENDED"
            strategy_action = "PROCEED IMMEDIATELY"
            status_color = "success"
        elif overall_score >= 130:
            strategy_status = "✅ RECOMMENDED"
            strategy_action = "PROCEED"
            status_color = "success"
        elif overall_score >= 110:
            strategy_status = "⚠️ CONDITIONAL"
            strategy_action = "PROCEED WITH OPTIMIZATIONS"
            status_color = "warning"
        elif overall_score >= 90:
            strategy_status = "🔄 REQUIRES MODIFICATION"
            strategy_action = "REVISE CONFIGURATION"
            status_color = "info"
        else:
            strategy_status = "❌ NOT RECOMMENDED"
            strategy_action = "RECONSIDER APPROACH"
            status_color = "error"
        
        # Executive Summary
        st.header("📋 Executive Summary")
        
        if status_color == "success":
            st.success(f"""
            **STRATEGIC RECOMMENDATION: {strategy_status}**
            
            **Action Required:** {strategy_action}
            
            **Services Analyzed:** {', '.join([s.upper() for s in selected_services])}
            
            **Primary Service:** {recommendations.get('primary_method', 'DataSync')}
            
            **Overall Strategy Score:** {overall_score:.0f}/170
            
            **Success Probability:** {85 + (overall_score - 100) * 0.2:.0f}%
            """)
        elif status_color == "warning":
            st.warning(f"""
            **STRATEGIC RECOMMENDATION: {strategy_status}**
            
            **Action Required:** {strategy_action}
            
            **Services Analyzed:** {', '.join([s.upper() for s in selected_services])}
            
            **Primary Service:** {recommendations.get('primary_method', 'DataSync')}
            
            **Overall Strategy Score:** {overall_score:.0f}/170
            
            **Success Probability:** {85 + (overall_score - 100) * 0.2:.0f}%
            """)
        else:
            st.error(f"""
            **STRATEGIC RECOMMENDATION: {strategy_status}**
            
            **Action Required:** {strategy_action}
            
            **Services Analyzed:** {', '.join([s.upper() for s in selected_services])}
            
            **Overall Strategy Score:** {overall_score:.0f}/170
            
            **Success Probability:** {max(50, 85 + (overall_score - 100) * 0.2):.0f}%
            """)
        
        # Project Overview
        st.header("📊 Multi-Service Project Overview")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Project", config['project_name'])
            st.metric("Services Enabled", len(selected_services))
        
        with col2:
            st.metric("Data Volume", f"{metrics['data_size_tb']:.1f} TB")
            st.metric("Primary Service", recommendations.get('primary_method', 'DataSync'))
        
        with col3:
            st.metric("Expected Throughput", f"{metrics['optimized_throughput']:.0f} Mbps")
            st.metric("Estimated Duration", f"{metrics['transfer_days']:.1f} days")
        
        with col4:
            st.metric("Total Investment", f"${metrics['cost_breakdown']['total']:,.0f}")
            st.metric("Risk Assessment", recommendations.get('risk_level', 'Medium'))
        
        # Service-specific conclusions
        if len(selected_services) > 1:
            st.header("🔧 Service-Specific Conclusions")
            
            for service in selected_services:
                if service in metrics.get('service_metrics', {}):
                    service_data = metrics['service_metrics'][service]
                    
                    with st.expander(f"{service.upper()} Analysis Summary"):
                        if service == 'datasync':
                            st.write(f"**Performance:** {service_data['throughput_mbps']:.0f} Mbps throughput")
                            st.write(f"**Efficiency:** {service_data['efficiency']:.1%} network utilization")
                            st.write(f"**Best for:** File and object migrations")
                            st.write(f"**Recommendation:** {'Primary choice' if recommendations.get('primary_method') == 'DataSync' else 'Secondary option'}")
                            
                        elif service == 'dms':
                            st.write(f"**Performance:** {service_data['throughput_mbps']:.0f} Mbps throughput")
                            st.write(f"**Migration Time:** {service_data['full_load_time_hours']:.1f} hours")
                            st.write(f"**CDC Lag:** {service_data['cdc_lag_minutes']:.0f} minutes")
                            st.write(f"**Best for:** Database migrations with minimal downtime")
                            st.write(f"**Recommendation:** {'Primary choice' if recommendations.get('primary_method') == 'DMS' else 'Specialized use'}")
                            
                        elif service == 'snowball':
                            st.write(f"**Devices Needed:** {service_data['devices_needed']}")
                            st.write(f"**Total Timeline:** {service_data['total_timeline_days']:.1f} days")
                            st.write(f"**Total Cost:** ${service_data['total_cost']:,.0f}")
                            st.write(f"**Best for:** Large datasets with limited bandwidth")
                            st.write(f"**Recommendation:** {'Primary choice' if recommendations.get('primary_method') == 'Snowball Edge' else 'Alternative option'}")
        
        # Implementation roadmap
        st.header("🛣️ Implementation Roadmap")
        
        if strategy_action == "PROCEED IMMEDIATELY" or strategy_action == "PROCEED":
            implementation_steps = [
                "1. ✅ **Executive Approval** - Secure final approval and budget allocation",
                "2. 🔧 **Service Setup** - Configure recommended services in order of priority",
                "3. 🌐 **Network Preparation** - Implement network optimizations and security controls",
                "4. 🧪 **Pilot Migration** - Begin with non-critical data using primary service",
                "5. 📊 **Performance Validation** - Monitor and validate against success criteria",
                "6. 🚀 **Full-Scale Migration** - Deploy all services according to strategy",
                "7. 📈 **Optimization & Monitoring** - Continuous improvement and monitoring"
            ]
        elif strategy_action == "PROCEED WITH OPTIMIZATIONS":
            implementation_steps = [
                "1. ⚠️ **Address Identified Issues** - Resolve performance and cost concerns",
                "2. 🔄 **Service Reconfiguration** - Apply AI optimization recommendations",
                "3. 💰 **Budget Reallocation** - Adjust budget based on refined estimates",
                "4. 🌐 **Network Upgrades** - Implement necessary bandwidth or pattern changes",
                "5. ✅ **Re-validation** - Confirm updated strategy meets requirements",
                "6. 📊 **Controlled Rollout** - Begin with enhanced configuration",
                "7. 📈 **Monitor and Adjust** - Continuous optimization during migration"
            ]
        else:
            implementation_steps = [
                "1. 🔄 **Strategy Review** - Fundamental reassessment of approach required",
                "2. 📊 **Requirements Analysis** - Re-evaluate business requirements and constraints",
                "3. 💰 **Budget and Timeline Review** - Assess feasibility of current parameters",
                "4. 🔧 **Alternative Service Evaluation** - Consider different service combinations",
                "5. 🤝 **Expert Consultation** - Engage AWS migration specialists",
                "6. 📋 **Revised Strategy Development** - Create new migration approach",
                "7. ⚖️ **Stakeholder Review** - Present alternatives to leadership"
            ]
        
        for step in implementation_steps:
            st.write(step)
        
        # Final AI recommendations
        st.header("🤖 Final AI Strategic Insights")
        
        if recommendations.get('ai_analysis'):
            st.markdown(f"""
            <div class="ai-insight">
                <strong>🔮 Advanced Claude AI Analysis:</strong><br>
                {recommendations['ai_analysis'].replace('\n', '<br>')}
            </div>
            """, unsafe_allow_html=True)
        
        # Service portfolio recommendation
        portfolio_analysis = f"""
        **Multi-Service Portfolio Analysis:** Your selection of {len(selected_services)} services provides 
        {'comprehensive' if len(selected_services) >= 3 else 'focused'} migration coverage. 
        The primary recommendation of {recommendations.get('primary_method', 'DataSync')} aligns with your 
        data characteristics and network capabilities. 
        {'Consider additional services for enhanced resilience.' if len(selected_services) < 2 else 'Good service diversity for risk mitigation.'}
        """
        
        st.info(portfolio_analysis)
        
        # PDF Report Generation
        st.header("📄 Generate Executive Report")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("📋 Generate Comprehensive PDF Report", type="primary"):
                if self.pdf_generator:
                    try:
                        pdf_buffer = self.pdf_generator.generate_comprehensive_report(config, metrics, recommendations)
                        if pdf_buffer:
                            st.download_button(
                                label="📥 Download Executive Report",
                                data=pdf_buffer.getvalue(),
                                file_name=f"{config['project_name']}_migration_strategy_report.pdf",
                                mime="application/pdf"
                            )
                            st.success("✅ Executive report generated successfully!")
                        else:
                            st.error("Failed to generate PDF report")
                    except Exception as e:
                        st.error(f"PDF generation error: {str(e)}")
                else:
                    st.warning("📋 PDF generation requires reportlab library")
        
        with col2:
            if st.button("💾 Save Project Configuration", type="secondary"):
                project_config = {
                    "project_name": config['project_name'],
                    "selected_services": selected_services,
                    "strategy_recommendation": strategy_status,
                    "primary_service": recommendations.get('primary_method'),
                    "performance_metrics": metrics,
                    "timestamp": datetime.now().isoformat()
                }
                
                st.session_state.migration_projects[config['project_name']] = project_config
                self.log_audit_event("PROJECT_SAVED", f"Multi-service project saved: {config['project_name']}")
                st.success(f"✅ Project configuration saved")
        
        st.success("🎯 **Multi-service migration analysis complete!** Use the recommendations above to proceed with your enterprise AWS migration strategy.")
    
    def safe_dataframe_display(self, df, use_container_width=True, hide_index=True, **kwargs):
        """Safely display DataFrame"""
        try:
            df_safe = df.astype(str)
            st.dataframe(df_safe, use_container_width=use_container_width, hide_index=hide_index, **kwargs)
        except Exception as e:
            st.error(f"Error displaying table: {str(e)}")
            st.write("Raw data:")
            st.write(df)
    
    def run(self):
        """Main application entry point"""
        self.render_header()
        self.render_navigation()
        
        # Get configuration
        config = self.render_sidebar_controls()
        
        # Detect configuration changes
        config_changed = self.detect_configuration_changes(config)
        
        # Calculate metrics
        metrics = self.calculate_migration_metrics(config)
        
        # Show real-time update indicator
        if config_changed:
            st.success("🔄 Configuration updated - Multi-service analysis refreshed")
        
        # Display last update time
        current_time = datetime.now()
        time_since_update = (current_time - self.last_update_time).seconds
        
        st.markdown(f"""
        <div style="text-align: right; color: #666; font-size: 0.8em; margin-bottom: 1rem;">
            <span class="real-time-indicator"></span>Last updated: {current_time.strftime('%H:%M:%S')} | 
            Services: {len(config.get('selected_services', []))} | Auto-refresh: {time_since_update}s ago
        </div>
        """, unsafe_allow_html=True)
        
        # Render appropriate tab
        if st.session_state.active_tab == "dashboard":
            self.render_dashboard_tab(config, metrics)
        elif st.session_state.active_tab == "multiservice":
            self.render_multiservice_tab(config, metrics)
        elif st.session_state.active_tab == "network":
            self.render_network_tab(config, metrics)
        elif st.session_state.active_tab == "performance":
            self.render_performance_tab(config, metrics)
        elif st.session_state.active_tab == "security":
            self.render_security_tab(config, metrics)
        elif st.session_state.active_tab == "analytics":
            self.render_analytics_tab(config, metrics)
        elif st.session_state.active_tab == "conclusion":
            self.render_conclusion_tab(config, metrics)
        
        # Update timestamp
        self.last_update_time = current_time

def main():
    """Main function to run the Complete Enterprise AWS Migration Platform"""
    try:
        platform = MigrationPlatform()
        platform.run()
        
    except Exception as e:
        st.error(f"An error occurred: {str(e)}")
        st.write("Please check your configuration and try again.")
        
        # Debug information
        st.write("**Debug Information:**")
        st.code(f"Error: {str(e)}")
        
        st.info("If the problem persists, please contact support.")

if __name__ == "__main__":
    main()