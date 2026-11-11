import Hero from '../components/Hero';
import WhyUs from '../components/WhyUs';
import CoreTechnology from '../components/CoreTechnology';
import DemoPreview from '../components/DemoPreview';

type HomeProps = {
  onOpenWorkbench: () => void;
};

export default function Home({ onOpenWorkbench }: HomeProps) {
  return (
    <>
      <Hero onAnalyze={onOpenWorkbench} />
      <WhyUs />
      <CoreTechnology />
      <DemoPreview onOpenWorkbench={onOpenWorkbench} />
    </>
  );
}
